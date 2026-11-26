"""Synthetic integration tests; no real applicants or private PDFs are loaded."""
import asyncio
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.api import bank_statements as banks, documents as docs
from backend.app.api.dependencies import get_current_principal, get_mongo_database
from backend.app.core.errors import APIError
from backend.app.services.aba_integration import (
    aba_to_legacy_ocr, analyze_aba_pdf, apply_correction, normalized_bank_quality, reviewed_analysis,
)
from backend.app.services.bank_statement_scoring import analyze_bank_statement
from backend.app.services.business_rules import DEFAULT_RULE_POLICY
from .aba_pdf_fixture import example_pdf


def nested(record, key):
    for name in key.split('.'):
        if not isinstance(record, dict) or name not in record:
            return None
        record = record[name]
    return record


class Collection:
    def __init__(self): self.rows = []; self.updates = []
    def matches(self, row, query):
        for key, wanted in query.items():
            if key == '$or':
                if not any(self.matches(row, branch) for branch in wanted): return False
                continue
            if key == '$and':
                if not all(self.matches(row, branch) for branch in wanted): return False
                continue
            actual = nested(row, key)
            if isinstance(wanted, dict):
                for operator, value in wanted.items():
                    if operator == '$in' and actual not in value: return False
                    if operator == '$nin' and actual in value: return False
                    if operator == '$ne' and actual == value: return False
                    if operator == '$exists' and (actual is not None) != value: return False
                    if operator == '$lte' and (actual is None or actual > value): return False
            elif actual != wanted: return False
        return True
    def find(self, query):
        async def to_list(length=None): return deepcopy([r for r in self.rows if self.matches(r, query)])
        return SimpleNamespace(to_list=to_list)
    async def find_one(self, query): return next((deepcopy(r) for r in self.rows if self.matches(r, query)), None)
    async def insert_one(self, row): self.rows.append(deepcopy(row))
    async def replace_one(self, query, replacement):
        for index, row in enumerate(self.rows):
            if self.matches(row, query): self.rows[index] = deepcopy(replacement); return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)
    async def update_one(self, query, changes):
        self.updates.append(deepcopy(changes))
        for row in self.rows:
            if not self.matches(row, query): continue
            for key, value in changes.get('$set', {}).items():
                target = row
                parts = key.split('.')
                for part in parts[:-1]: target = target.setdefault(part, {})
                target[parts[-1]] = deepcopy(value)
            for key in changes.get('$unset', {}): row.pop(key, None)
            for key, value in changes.get('$push', {}).items(): row.setdefault(key, []).append(deepcopy(value))
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class Database(dict):
    def __missing__(self, key): self[key] = Collection(); return self[key]


@pytest.fixture
def context(monkeypatch, tmp_path):
    db = Database(); scheduled = []
    requirement = {'_id':'bank','requirementId':'bank','visaTypeId':'visa','name':'Bank statement','code':'bank_statement',
        'status':'ACTIVE','required':True,'requiredForFinalAssessment':True,'weight':100,'scoringMode':'DYNAMIC',
        'minimumEarnedPoints':'0','maximumEarnedPoints':'100','qualityScorer':'ABA_STATEMENT_V1',
        'qualityScorerVersion':'1.0','mappingVersion':'bank-linear-1-to-10-v1',
        'acceptedFormats':['pdf'],'allowMultiple':False,'maxFileSize':10*1024*1024}
    snapshot={'id':'policy-1','version':'1','scoringVersion':'evidence-v3','requirements':[deepcopy(requirement)]}
    application={'_id':'application','visaTypeId':'visa','customer':'owner','requirementSnapshot':[deepcopy(requirement)],
        'evidencePolicySnapshot':snapshot,'policyVersionId':'policy-1','scoringVersion':'evidence-v3',
        'predictionStatus':'COMPLETED','approvalProbability':.8}
    db['visarequirements'].rows.append(deepcopy(requirement));db['visaapplications'].rows.append(application)
    monkeypatch.setattr(banks,'_PRIVATE_DIR',tmp_path);monkeypatch.setattr(docs,'_PRIVATE_DIR',tmp_path)
    monkeypatch.setattr(docs,'_schedule_document_ocr',lambda document,database,attempt: scheduled.append((deepcopy(document),attempt)) or True)
    def forbid_external(*args,**kwargs): raise AssertionError('Native ABA document must not call external OCR')
    monkeypatch.setattr(docs.httpx,'AsyncClient',forbid_external)
    app=FastAPI();app.include_router(banks.router,prefix='/api/bank-statements');app.include_router(docs.router,prefix='/api/documents')
    app.dependency_overrides[get_mongo_database]=lambda:db
    async def principal(request:Request):
        token=request.headers.get('authorization','')
        if not token.startswith('Bearer '):raise APIError(401,'Authentication required')
        role=token[7:]
        if role not in {'STAFF','ADMIN','CUSTOMER'}:raise APIError(401,'Invalid role')
        return {'id':'owner' if role=='CUSTOMER' else 'reviewer','email':'synthetic@example.test','role':role}
    app.dependency_overrides[get_current_principal]=principal
    @app.exception_handler(APIError)
    async def error(_request,error): return JSONResponse(status_code=error.status_code,content={'message':error.message})
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,db=db,scheduled=scheduled,tmp=tmp_path,headers={'Authorization':'Bearer STAFF'})
    docs._PDF_PASSWORDS.clear()


def upload_and_process(c, raw=None, password=None):
    raw=raw or example_pdf(rows=[])
    data={'applicationId':'application','requirementId':'bank'}
    if password is not None:data['pdfPassword']=password
    response=c.client.post('/api/documents/upload',headers=c.headers,data=data,files={'file':('Synthetic.pdf',raw,'application/pdf')})
    assert response.status_code==201,response.text
    document_id=response.json()['document']['_id']
    document,attempt=c.scheduled[-1]
    asyncio.run(docs._process_document_ocr(document,c.db,attempt))
    document=asyncio.run(c.db['documents'].find_one({'_id':document_id}))
    return document


def final_page_summary_pdf():
    doc=fitz.open()
    start=date(2026,1,1);end=date(2026,3,31)
    for number in range(2):
        page=doc.new_page(width=595,height=842)
        def text(x,y,value,size=9):page.insert_text((x,y),value,fontsize=size)
        if number==0:
            text(50,40,'ABA BANK',18);text(310,40,'ACCOUNT STATEMENT',16)
            text(310,62,f'For period: {start:%b %d, %Y} - {end:%b %d, %Y}')
            text(300,130,'Account Holder Name');text(430,130,'SYNTHETIC SPONSOR')
            text(300,150,'Account No.');text(430,150,'000000001')
            text(300,170,'Account Currency');text(430,170,'USD')
            text(300,230,'Opening Balance');text(430,230,'0.00 USD')
            table=345
        else:
            table=70
            text(50,200,'Ending Balance');text(430,200,'0.00 USD')
            text(50,220,'Total Money In');text(430,220,'0.00 USD')
            text(50,240,'Total Money Out');text(430,240,'0.00 USD')
            text(50,260,'Total 0.00 USD')
        for x,label in [(50,'Date'),(120,'Transaction Details'),(330,'Money In'),(400,'Money Out'),(510,'Balance')]:
            text(x,table,label)
        text(50,815,'Advanced Bank of Asia Ltd. - Synthetic fixture')
        text(510,815,f'Page {number+1}')
    data=doc.tobytes();doc.close()
    return data


def test_bank_statement_upload_uses_50mb_limit(context, monkeypatch):
    c=context
    assert docs.BANK_STATEMENT_MAX_FILE_SIZE == 50 * 1024 * 1024
    monkeypatch.setattr(docs, 'BANK_STATEMENT_MAX_FILE_SIZE', 64)
    response=c.client.post(
        '/api/documents/upload',
        headers=c.headers,
        data={'applicationId':'application','requirementId':'bank'},
        files={'file':('Synthetic.pdf',b'%PDF-1.7\n' + b'x' * 64,'application/pdf')},
    )
    assert response.status_code == 413


def test_transaction_source_review_persists_staff_audit_and_configured_penalty(context, monkeypatch):
    c=context
    fields={
        'bankName':'Synthetic Bank','accountHolderName':'SYNTHETIC SPONSOR','accountNumber':'000000001',
        'openingBalance':4000,'closingBalance':5000,'currency':'USD',
        'statementStartDate':'2026-01-01','statementEndDate':'2026-03-31',
        'deposits':2000,'withdrawals':999.99,'averageBalance':4500,
        'transactionHistory':[
            {'date':'2026-03-20','description':'Wire transfer','amount':2000,'type':'credit','balance':6000,'page':2,'referenceNumber':'WIRE-1'},
            {'date':'2026-03-25','description':'Rent payment','amount':999.99,'type':'debit','balance':5000,'page':2,'referenceNumber':'RENT-1'},
        ],
        'monthlyClosingBalances':[
            {'month':'2026-01','closingBalance':4000,'currency':'USD','page':1},
            {'month':'2026-02','closingBalance':4500,'currency':'USD','page':1},
            {'month':'2026-03','closingBalance':5000,'currency':'USD','page':2},
        ],
    }
    analysis=analyze_bank_statement(fields)
    document={'_id':'source-review-doc','applicationId':'application','requirementCode':'bank_statement',
        'status':'NEEDS_REVIEW','ocrStatus':'OCR_COMPLETED','extractedFields':fields,
        'bankStatementAnalysis':analysis,
        'transactionSourceReviews':{'0':{'evidenceReferences':['legacy:statement-page-2']}}}
    c.db['documents'].rows.append(deepcopy(document))
    async def no_invalidate(*_args,**_kwargs): return None
    monkeypatch.setattr(banks,'_invalidate_aba_case',no_invalidate)

    below_threshold=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/1/source-review',
        headers=c.headers,
        json={'action':'SOURCE_UNVERIFIED','sourceCategory':'PERSONAL_SAVINGS','notes':'This transaction is below the configured amount threshold.'},
    )
    assert below_threshold.status_code==404

    missing_category=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'CONFIRM_SOURCE','notes':'The transfer receipt was reviewed and recorded.'},
    )
    assert missing_category.status_code==400

    unknown_source_confirm=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'CONFIRM_SOURCE','sourceCategory':'UNKNOWN','notes':''},
    )
    assert unknown_source_confirm.status_code==400

    missing_unverified_category=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'SOURCE_UNVERIFIED','notes':''},
    )
    assert missing_unverified_category.status_code==400

    missing_other_confirm_note=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'CONFIRM_SOURCE','sourceCategory':'OTHER','notes':'   '},
    )
    assert missing_other_confirm_note.status_code==400

    missing_other_unverified_note=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'SOURCE_UNVERIFIED','sourceCategory':'OTHER','notes':'   '},
    )
    assert missing_other_unverified_note.status_code==400

    pending=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'REQUEST_MORE_EVIDENCE','sourceCategory':'UNKNOWN','notes':'','requestedEvidence':'Provide transfer receipt.'},
    )
    assert pending.status_code==200,pending.text
    pending_score=pending.json()['bankStatementAnalysis']['bankStatementScore']
    assert pending_score['pendingTransactionReviews']==1
    assert pending_score['totalPenaltyPoints']==0
    assert pending_score['transactionReviews'][0]['status']=='PENDING_REVIEW'
    assert pending_score['transactionReviews'][0]['penaltyPoints']==0
    assert pending_score['transactionReviews'][0]['sourceDeduction']=={
        'points':0.0,'status':'PENDING_REVIEW'
    }
    assert pending_score['transactionReviews'][0]['amount']==2000
    assert pending_score['transactionReviews'][0]['direction']=='credit'
    assert pending_score['transactionReviews'][0]['reference']=='WIRE-1'
    assert pending_score['transactionReviews'][0]['transactionFlags'][0]['flagType']=='MONEY_IN_THRESHOLD'
    assert pending_score['transactionReviews'][0]['transactionFlags'][0]['deductionPoints']==0
    assert pending_score['transactionReviews'][0]['transactionFlags'][0]['deductionStatus']=='INFORMATIONAL_ONLY'
    assert pending_score['transactionReviews'][0]['appliedDeductions']==[]
    assert pending_score['transactionReviews'][0]['sourceCategory']=='UNKNOWN'

    pending_policy=deepcopy(DEFAULT_RULE_POLICY)
    pending_policy['bank_statement_scoring']['deduct_pending_source_review']=True
    c.db['business_rules'].rows.append({
        '_id':'visa_financial_rules','policy':pending_policy
    })
    pending_with_deduction=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'REQUEST_MORE_EVIDENCE','sourceCategory':'UNKNOWN','notes':'','requestedEvidence':'Provide a dated transfer receipt.'},
    )
    assert pending_with_deduction.status_code==200,pending_with_deduction.text
    pending_with_deduction_score=pending_with_deduction.json()['bankStatementAnalysis']['bankStatementScore']
    assert pending_with_deduction_score['transactionReviews'][0]['status']=='PENDING_REVIEW'
    assert pending_with_deduction_score['totalPenaltyPoints']==0
    assert pending_with_deduction_score['transactionReviews'][0]['sourceDeduction']=={
        'points':0.0,'status':'PENDING_REVIEW'
    }
    assert pending_with_deduction.json()['audit']['penaltyPoints']==0

    unverified=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'SOURCE_UNVERIFIED','sourceCategory':'UNKNOWN','notes':''},
    )
    assert unverified.status_code==200,unverified.text
    unverified_score=unverified.json()['bankStatementAnalysis']['bankStatementScore']
    assert unverified_score['totalPenaltyPoints']==0.5
    assert unverified.json()['audit']['status']=='UNVERIFIED'
    assert unverified.json()['audit']['penaltyPoints']==0.5
    assert unverified.json()['audit']['reviewerId']=='reviewer'
    assert unverified.json()['bankStatementAnalysis']['staffReviewStatus']=='VERIFIED'
    assert unverified.json()['bankStatementAnalysis']['sourceReviewStatus']=='UNVERIFIED'
    assert unverified_score['transactionReviews'][0]['status']=='UNVERIFIED'
    assert unverified_score['transactionReviews'][0]['sourceDeduction']=={
        'points':0.5,'status':'APPLIED'
    }
    assert sum(
        item['points']
        for item in unverified_score['transactionReviews'][0]['appliedDeductions']
    )==0.5
    assert unverified_score['components']['transaction_history']['points'] == (
        pending_score['components']['transaction_history']['points'] - 0.5
    )
    assert unverified_score['points']==round(
        sum(item['points'] for item in unverified_score['components'].values()),2
    )
    unverified_analysis=unverified.json()['bankStatementAnalysis']
    assert unverified_analysis['computedBankScore']['points']==unverified_score['points']
    assert unverified_analysis['requirementContribution']['points']==unverified_score['points']
    saved_unverified=asyncio.run(c.db['documents'].find_one({'_id':'source-review-doc'}))
    assert saved_unverified['bankStatementAnalysis']['staffReviewStatus']=='VERIFIED'
    assert saved_unverified['bankStatementAnalysis']['sourceReviewStatus']=='UNVERIFIED'
    assert saved_unverified['bankStatementAnalysis']['computedBankScore']['points']==unverified_score['points']

    confirmed=c.client.post(
        '/api/bank-statements/documents/source-review-doc/transactions/0/source-review',
        headers=c.headers,
        json={'action':'CONFIRM_SOURCE','sourceCategory':'SALARY','notes':'   '},
    )
    assert confirmed.status_code==200,confirmed.text
    confirmed_score=confirmed.json()['bankStatementAnalysis']['bankStatementScore']
    assert confirmed_score['totalPenaltyPoints']==0
    assert confirmed_score['points']==pending_score['points']
    assert confirmed.json()['bankStatementAnalysis']['computedBankScore']['points']==confirmed_score['points']
    stored=asyncio.run(c.db['documents'].find_one({'_id':'source-review-doc'}))
    assert len(stored['transactionSourceReviewAudit'])==4
    assert stored['transactionSourceReviews']['0']['status']=='CONFIRMED'
    assert stored['transactionSourceReviews']['0']['evidenceReferences']==['legacy:statement-page-2']
    assert stored['transactionSourceReviews']['0']['notes']==''
    assert stored['transactionSourceReviews']['0']['sourceCategory']=='SALARY'
    assert stored['transactionSourceReviews']['0']['sourceCategoryLabel']=='Salary / ប្រាក់ខែ'
    assert stored['transactionSourceReviews']['0']['transactionReference']=='WIRE-1'
    assert stored['transactionSourceReviews']['0']['transactionAmount']==2000
    assert stored['transactionSourceReviews']['0']['transactionCurrency']=='USD'
    assert stored['transactionSourceReviews']['0']['amountUsd']==2000
    assert stored['transactionSourceReviews']['0']['exchangeRateToUsd']==1
    assert stored['transactionSourceReviewAudit'][-1]['staffReviewThresholdUsd']==1000
    assert stored['transactionSourceReviewAudit'][-1]['transactionAmount']==2000
    assert stored['transactionSourceReviewAudit'][-1]['evidenceReferences']==['legacy:statement-page-2']
    assert [item['status'] for item in stored['transactionSourceReviews']['0']['decisionHistory']] == [
        'PENDING_REVIEW',
        'PENDING_REVIEW',
        'UNVERIFIED',
        'CONFIRMED',
    ]
    assert stored['bankStatementAnalysis']['bankStatementScore']['totalPenaltyPoints']==0
    assert stored['bankStatementAnalysis']['bankStatementScore']['transactionReviews'][0]['sourceDeduction']=={
        'points':0.0,'status':'SOURCE_CONFIRMED'
    }


def review(c, document, **overrides):
    analysis=document['abaStatementAnalysis']
    payload={'analysisRevision':analysis['analysisRevision'],'status':'VERIFIED','reason':'Checked synthetic financial evidence.',
             **overrides}
    return c.client.post(f'/api/bank-statements/documents/{document["_id"]}/aba/review',headers=c.headers,json=payload)


def ready_verification_fixture(c):
    analysis=analyze_aba_pdf(example_pdf(rows=[(date(2026,1,2),10000,0,'Synthetic income')]),'Synthetic.pdf')
    analysis.update({
        'reviewBlockers':[],
        'reconciliationStatus':'RECONCILED',
        'financialEvidenceStatus':'RECONCILED',
    })
    analysis['score']['provisional']=False
    fields=aba_to_legacy_ocr(analysis)['fields']
    fields['transactionHistory']=[{'date':'2026-01-02','amount':100,'direction':'credit'}]
    weights={'ending_balance':22.5,'starting_balance':6.75,'transaction_history':9.0,'transaction_period':6.75}
    components={key:{'points':5.75 if key=='transaction_history' else maximum,'maximum':maximum,'available':True}
                for key,maximum in weights.items()}
    bank_analysis={
        'financialRequirementStatus':'PASS',
        'bankStatementScore':{
            'points':41.75,'maximum':45,'available':True,'provisional':False,
            'components':components,'transactionReviews':[],'pendingTransactionReviews':0,
            'totalPenaltyPoints':0.0,
        },
    }
    document={
        '_id':'verify-statement','applicationId':'application','requirementCode':'bank_statement',
        'status':'NEEDS_REVIEW','ocrStatus':'OCR_COMPLETED','extractedFields':fields,
        'abaStatementAnalysis':analysis,'bankStatementAnalysis':bank_analysis,
    }
    c.db['documents'].rows.append(deepcopy(document))
    return document,bank_analysis


def test_one_click_bank_verification_persists_confirmed_score_and_audit(context,monkeypatch):
    c=context
    document,bank_analysis=ready_verification_fixture(c)
    async def no_cross_document_refresh(*_args,**_kwargs): return None
    monkeypatch.setattr(banks,'score_bank_statement',lambda *_args,**_kwargs:deepcopy(bank_analysis))
    monkeypatch.setattr(docs,'_refresh_cross_document_consistency',no_cross_document_refresh)
    url=f'/api/bank-statements/documents/{document["_id"]}/aba/verify'
    payload={'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],'reason':'Reviewed required financial evidence.'}

    response=c.client.post(url,headers=c.headers,json=payload)
    assert response.status_code==200,response.text
    body=response.json()
    assert body['staffReviewStatus']=='CONFIRMED'
    assert body['documentReviewStatus']=='CONFIRMED'
    assert body['financialEvidenceStatus']=='VERIFIED'
    assert body['confirmedBankScore']=={'points':41.75,'maximum':45.0,'status':'CONFIRMED'}
    bank_score=body['bankStatementAnalysis']['bankStatementScore']
    assert bank_score['points']==41.75
    assert bank_score['components']['transaction_history']['points']==5.75
    assert body['bankStatementAnalysis']['computedBankScore']['points']==41.75
    assert body['bankStatementAnalysis']['requirementContribution']['points']==41.75
    assert bank_score['points']==round(sum(item['points'] for item in bank_score['components'].values()),2)
    assert body['bankStatementAnalysis']['confirmedBankScore']['status']=='CONFIRMED'
    assert body['verificationBlockers']==[]
    assert body['analysis']['authenticityStatus']=='NOT_VERIFIED'
    assert body['analysis']['reconciliationStatus']=='RECONCILED'
    assert body['analysis']['financialEvidenceStatus']=='VERIFIED'
    assert body['analysis']['monthlyBalanceVerification']==document['abaStatementAnalysis']['monthlyBalanceVerification']
    assert body['reviewerId']=='reviewer'
    assert body['reviewNote']=='Reviewed required financial evidence.'
    assert body['manualReviewHistory'][-1]['reviewerId']=='reviewer'
    assert body['manualReviewHistory'][-1]['note']=='Reviewed required financial evidence.'
    application_score=c.db['visaapplications'].rows[0]['evidenceAssessment']
    assert application_score['confirmedReadinessPoints']==0
    assert application_score['maximumPoints']==100
    stored=asyncio.run(c.db['documents'].find_one({'_id':document['_id']}))
    assert stored['status']=='VERIFIED'
    assert stored['reviewerId']=='reviewer'
    assert stored['reviewNote']=='Reviewed required financial evidence.'
    assert stored['manualReviewHistory'][-1]['reviewerId']=='reviewer'
    assert stored['bankStatementAnalysis']['bankStatementScore']['totalPenaltyPoints']==0.0
    duplicate=c.client.post(url,headers=c.headers,json={
        'analysisRevision':body['analysis']['analysisRevision'],'reason':'Attempt duplicate verification.',
    })
    assert duplicate.status_code==409
    assert 'already been verified' in duplicate.json()['message']


def test_one_click_verification_keeps_unverified_source_aggregate_and_penalty(context,monkeypatch):
    c=context
    document,bank_analysis=ready_verification_fixture(c)
    review={
        'status':'UNVERIFIED',
        'reviewerId':'reviewer',
        'reviewedAt':'2026-11-21T00:00:00+00:00',
        'sourceCategory':'UNKNOWN',
        'penaltyPoints':0.5,
    }
    bank_score=bank_analysis['bankStatementScore']
    bank_score['points']=29.79
    bank_score['totalPenaltyPoints']=0.5
    bank_score['components']['ending_balance']['points']=7.88
    bank_score['components']['starting_balance']['points']=6.66
    bank_score['components']['transaction_history']['points']=8.5
    bank_score['transactionReviews']=[{
        'transactionIndex':3,
        'requiresStaffReview':True,
        'status':'UNVERIFIED',
        'reviewerId':review['reviewerId'],
        'reviewedAt':review['reviewedAt'],
        'sourceCategory':'UNKNOWN',
        'penaltyPoints':0.5,
    }]
    document['transactionSourceReviews']={'3':review}
    c.db['documents'].rows[-1]['transactionSourceReviews']=deepcopy(document['transactionSourceReviews'])
    c.db['documents'].rows[-1]['bankStatementAnalysis']=deepcopy(bank_analysis)
    async def no_invalidate(*_args,**_kwargs): return None
    async def no_cross_document_refresh(*_args,**_kwargs): return None
    monkeypatch.setattr(banks,'score_bank_statement',lambda *_args,**_kwargs:deepcopy(bank_analysis))
    monkeypatch.setattr(banks,'_invalidate_aba_case',no_invalidate)
    monkeypatch.setattr(docs,'_refresh_cross_document_consistency',no_cross_document_refresh)

    response=c.client.post(
        f'/api/bank-statements/documents/{document["_id"]}/aba/verify',
        headers=c.headers,
        json={
            'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],
            'reason':'Reviewed unverified source evidence.',
        },
    )
    assert response.status_code==200,response.text
    finalized=response.json()['bankStatementAnalysis']
    finalized_score=finalized['bankStatementScore']
    assert finalized_score['components']['transaction_history']['points']==8.5
    assert finalized_score['points']==29.79
    assert finalized_score['totalPenaltyPoints']==0.5
    assert finalized['computedBankScore']['points']==29.79
    assert finalized['requirementContribution']['points']==29.79
    assert finalized['sourceReviewStatus']=='UNVERIFIED'
    assert finalized['confirmedBankScore']=={
        'points':29.79,'maximum':45.0,'status':'CONFIRMED',
    }
    assert response.json()['staffReviewStatus']=='CONFIRMED'
    assert response.json()['documentReviewStatus']=='CONFIRMED'
    assert response.json()['analysis']['authenticityStatus']=='NOT_VERIFIED'


def test_one_click_bank_verification_explains_pending_transaction_review(context):
    c=context
    document,bank_analysis=ready_verification_fixture(c)
    bank_analysis['bankStatementScore']['transactionReviews']=[{
        'transactionIndex':3,'requiresStaffReview':True,'status':'PENDING_REVIEW',
    }]
    document['bankStatementAnalysis']=bank_analysis
    c.db['documents'].rows[-1]['bankStatementAnalysis']=deepcopy(bank_analysis)
    url=f'/api/bank-statements/documents/{document["_id"]}/aba/verify'
    response=c.client.post(url,headers=c.headers,json={
        'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],
        'reason':'Reviewed required financial evidence.',
    })
    assert response.status_code==409
    assert 'transaction(s): 3' in response.json()['message']
    analysis_response=c.client.get(
        f'/api/bank-statements/documents/{document["_id"]}/aba',
        headers=c.headers,
    )
    assert analysis_response.status_code==200
    assert any('transaction(s): 3' in item for item in analysis_response.json()['verificationBlockers'])
    stored=asyncio.run(c.db['documents'].find_one({'_id':document['_id']}))
    assert stored['status']=='NEEDS_REVIEW'
    assert 'staffReviewStatus' not in stored


def test_one_click_verification_requires_a_saved_source_review_outcome(context):
    c=context
    document,bank_analysis=ready_verification_fixture(c)
    bank_analysis['bankStatementScore']['transactionReviews']=[{
        'transactionIndex':3,'requiresStaffReview':True,'status':'CONFIRMED',
        'reviewerId':'reviewer','reviewedAt':'2026-11-21T00:00:00+00:00',
        'sourceCategory':'SALARY',
    }]
    c.db['documents'].rows[-1]['bankStatementAnalysis']=deepcopy(bank_analysis)
    response=c.client.post(
        f'/api/bank-statements/documents/{document["_id"]}/aba/verify',
        headers=c.headers,
        json={
            'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],
            'reason':'Reviewed required financial evidence.',
        },
    )
    assert response.status_code==409
    assert 'Source review is unresolved for transaction(s): 3' in response.json()['message']
    stored=asyncio.run(c.db['documents'].find_one({'_id':document['_id']}))
    assert stored['status']=='NEEDS_REVIEW'
    assert 'staffReviewStatus' not in stored


def test_one_click_bank_verification_requires_document_checklist_completion(context):
    c=context
    document,_=ready_verification_fixture(c)
    document['extractedFields'].pop('accountNumber')
    c.db['documents'].rows[-1]['extractedFields'].pop('accountNumber')
    response=c.client.post(
        f'/api/bank-statements/documents/{document["_id"]}/aba/verify',
        headers=c.headers,
        json={
            'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],
            'reason':'Reviewed required financial evidence.',
        },
    )
    assert response.status_code==409
    assert 'document review checklist items are incomplete: accountNumber' in response.json()['message']
    assert c.db['documents'].rows[-1]['status']=='NEEDS_REVIEW'


def test_one_click_bank_verification_accepts_confirmed_source_without_reference(context,monkeypatch):
    c=context
    document,bank_analysis=ready_verification_fixture(c)
    saved_review={
        'status':'CONFIRMED',
        'reviewerId':'reviewer',
        'reviewedAt':'2026-11-21T00:00:00+00:00',
        'sourceCategory':'SALARY',
    }
    bank_analysis['bankStatementScore']['transactionReviews']=[{
        'transactionIndex':3,'requiresStaffReview':True,'status':'CONFIRMED',
        'reviewerId':saved_review['reviewerId'],'reviewedAt':saved_review['reviewedAt'],
        'sourceCategory':'SALARY','evidenceReferences':[],
    }]
    document['transactionSourceReviews']={'3':saved_review}
    c.db['documents'].rows[-1]['transactionSourceReviews']=deepcopy(document['transactionSourceReviews'])
    c.db['documents'].rows[-1]['bankStatementAnalysis']=deepcopy(bank_analysis)
    async def no_invalidate(*_args,**_kwargs): return None
    async def no_cross_document_refresh(*_args,**_kwargs): return None
    monkeypatch.setattr(banks,'score_bank_statement',lambda *_args,**_kwargs:deepcopy(bank_analysis))
    monkeypatch.setattr(banks,'_invalidate_aba_case',no_invalidate)
    monkeypatch.setattr(docs,'_refresh_cross_document_consistency',no_cross_document_refresh)
    response=c.client.post(
        f'/api/bank-statements/documents/{document["_id"]}/aba/verify',
        headers=c.headers,
        json={
            'analysisRevision':document['abaStatementAnalysis']['analysisRevision'],
            'reason':'Reviewed required financial evidence.',
        },
    )
    assert response.status_code==200,response.text
    assert response.json()['staffReviewStatus']=='CONFIRMED'
    assert response.json()['bankStatementAnalysis']['sourceReviewStatus']=='CONFIRMED'


def test_bank_normalization_and_major_unit_boundary():
    a=analyze_aba_pdf(example_pdf(rows=[]),'Synthetic.pdf')
    assert a['statementStrengthScore']==5.5
    assert normalized_bank_quality(5.5)=='0.5'
    assert normalized_bank_quality(None) is None
    legacy=aba_to_legacy_ocr(a)
    assert a['metrics']['opening']==450000 and legacy['fields']['openingBalance']==4500
    assert legacy['fields']['derivedMonthlyBalances'][0]['evidenceType']=='DERIVED_DAILY_CLOSING'
    assert legacy['fields']['bankName']=='ABA Bank'
    assert a['monthlyBalanceVerification']['months'][0]['sourcePage']==1
    assert legacy['confidence'] is None


def test_final_page_summary_fields_keep_page_evidence_without_holder_matching():
    analysis=analyze_aba_pdf(final_page_summary_pdf(),'Applicant-Name.pdf')
    statement=analysis['statement']
    assert analysis['reconciliationStatus']=='RECONCILED'
    assert statement['ending']==0 and statement['reported_in']==0 and statement['reported_out']==0
    assert statement['metadata_sources']['ending']['page']==2
    assert statement['metadata_sources']['reported_in']['page']==2
    assert statement['metadata_sources']['reported_out']['page']==2
    assert statement['holder']=='SYNTHETIC SPONSOR'
    assert statement['identity_notices']==[]
    assert analysis['accountHolderExtractionStatus']=='EXTRACTED'
    assert analysis['authenticityStatus']=='NOT_VERIFIED'
    assert analysis['monthlyBalanceVerification']['months'][-1]['evidenceType']=='DERIVED_DAILY_CLOSING'


def test_monthly_closing_balance_retains_last_posted_transaction_source():
    analysis=analyze_aba_pdf(example_pdf(),'Synthetic.pdf')
    march=next(item for item in analysis['monthlyBalanceVerification']['months'] if item['month']=='2026-03')
    assert march['sourceDate']=='2026-03-31'
    assert march['sourcePage']==1
    assert march['sourceTransaction']==5
    assert march['evidenceType']=='DERIVED_DAILY_CLOSING'


def test_conflicting_summary_balance_candidates_are_not_selected():
    source=fitz.open(stream=final_page_summary_pdf(),filetype='pdf')
    source[1].insert_text((50,280),'Ending Balance 123.00 USD',fontsize=9)
    raw=source.tobytes();source.close()
    analysis=analyze_aba_pdf(raw,'Applicant-Name.pdf')
    conflict=next(issue for issue in analysis['issues'] if issue['code']=='summary_field_conflict')
    assert analysis['statement']['ending'] is None
    assert conflict['field']=='ending'
    candidates=analysis['statement']['metadata_sources']['ending']['candidates']
    assert [item['value'] for item in candidates]==[0,12300]
    assert all(item['page']==2 for item in candidates)
    assert all('Ending Balance' in item['raw'] for item in candidates)
    review_blocker=next(item for item in analysis['reviewBlockers'] if item['code']=='summary_field_conflict')
    assert [item['value'] for item in review_blocker['candidates']]==[0,123]


def test_standalone_and_application_upload_share_local_reader(context):
    c=context;raw=example_pdf(rows=[])
    standalone=c.client.post('/api/bank-statements/analyze',headers=c.headers,files={'file':('Synthetic.pdf',raw,'application/pdf')})
    assert standalone.status_code==201,standalone.text
    assert standalone.json()['result']['score']['ocr_confidence'] is None
    doc=upload_and_process(c,raw)
    assert doc['abaStatementAnalysis']['statementStrengthScore']==standalone.json()['abaStatementAnalysis']['statementStrengthScore']==5.5
    assert doc['status']=='NEEDS_REVIEW' and doc['awardedPoints']==0
    assert doc['ocrConfidence'] is None
    assert doc['abaStatementAnalysis']['ocrStatus']=='OCR_COMPLETED'
    assert doc['abaStatementAnalysis']['authenticityStatus']=='NOT_VERIFIED'
    assert 'relationship' not in doc['ocrMessage'].lower()
    assert any('Reading ABA page 1 of 1' in u.get('$set',{}).get('ocrMessage','') for u in c.db['documents'].updates)
    app=c.db['visaapplications'].rows[0]
    assert app['scoringVersion']=='evidence-v3'
    assert app['evidenceAssessment']['confirmedReadinessPoints']==0
    assert app['approvalProbability'] is None


def test_review_and_primary_selection_do_not_require_account_relationship(context):
    c=context;doc=upload_and_process(c)
    response=review(c,doc);assert response.status_code==200,response.text
    reviewed=response.json()['analysis']
    assert reviewed['reviewStatus']=='VERIFIED'
    assert reviewed['statement']['holder']=='SYNTHETIC EXAMPLE'
    assert reviewed['ownerRelationshipConfirmed'] is False
    assert reviewed['authenticityStatus']=='NOT_VERIFIED'
    assert 'ownerRelationship' not in reviewed['audit'][-1]
    assert c.db['visaapplications'].rows[0]['evidenceAssessment']['confirmedReadinessPoints']==0
    selected=c.client.post(f'/api/bank-statements/documents/{doc["_id"]}/aba/select-primary',headers=c.headers,
        json={'analysisRevision':reviewed['analysisRevision'],'reason':'Selected applicant primary statement.'})
    assert selected.status_code==200,selected.text
    assert selected.json()['selectedPrimary'] is True
    app=c.db['visaapplications'].rows[0]
    assert app['selectedBankAnalysisRevision']==reviewed['analysisRevision']
    assert app['evidenceAssessment']['confirmedReadinessPoints']==50
    assert app['evidenceAssessment']['maximumPoints']==100
    # Re-review rotates the analysis revision, so a previously selected account
    # must expose the primary action again rather than leaving the UI disabled.
    current=asyncio.run(c.db['documents'].find_one({'_id':doc['_id']}))
    rerun=review(c,current)
    assert rerun.status_code==200 and rerun.json()['selectedPrimary'] is False
    assert c.db['visaapplications'].rows[0]['evidenceAssessment']['confirmedReadinessPoints']==0


def test_source_preview_and_customer_authorization(context):
    c=context;doc=upload_and_process(c);url=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    assert c.client.get(url).status_code==401
    assert c.client.get(url,headers={'Authorization':'Bearer CUSTOMER'}).status_code==403
    verify_url=f'/api/bank-statements/documents/{doc["_id"]}/aba/verify'
    assert c.client.post(verify_url,json={}).status_code==401
    assert c.client.post(verify_url,headers={'Authorization':'Bearer CUSTOMER'},json={}).status_code==403
    response=c.client.get(url+'/page/1',headers=c.headers)
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert response.headers['cache-control']=='no-store'
    assert c.client.get(url+'/page/2',headers=c.headers).status_code==404


def test_stale_review_cannot_overwrite_new_analysis(context):
    c=context;raw=example_pdf(rows=[(date(2026,1,2),10000,0,'Synthetic credit REF# ONE')]);doc=upload_and_process(c,raw)
    old=doc['abaStatementAnalysis']['analysisRevision'];base=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    changed=c.client.post(base+'/corrections',headers=c.headers,json={'analysisRevision':old,'row':1,'field':'balance','value':'4599.91','reason':'Synthetic inconsistency for review test.','sourceResolutionConfirmed':True})
    assert changed.status_code==200,changed.text
    analysis=changed.json()['analysis']
    assert analysis['statementStrengthScore'] is None
    assert any(abs(i.get('difference',0))==9 for i in analysis['issues'])
    rejected=review(c,doc)
    assert rejected.status_code==409
    fresh=deepcopy(doc);fresh['abaStatementAnalysis']=analysis
    blocked=review(c,fresh)
    assert blocked.status_code==409 and 'reconciliation' in blocked.json()['message'].lower()
    persisted=c.db['documents'].rows[0]
    assert persisted['extractedFields']['transactionHistory'][0]['balance']==4599.91


def test_documented_correction_resolution_never_waives_arithmetic(context):
    c=context;doc=upload_and_process(c,example_pdf(rows=[(date(2026,1,2),10000,0,'Synthetic credit REF# ONE')]))
    base=f'/api/bank-statements/documents/{doc["_id"]}/aba'
    a=doc['abaStatementAnalysis']
    changed=c.client.post(base+'/corrections',headers=c.headers,json={'analysisRevision':a['analysisRevision'],'row':1,'field':'balance','value':'4599.91','reason':'Synthetic extraction correction.'}).json()['analysis']
    assert any(i['code']=='correction_requires_resolution' for i in changed['issues'])
    payload={'analysisRevision':changed['analysisRevision'],'status':'VERIFIED','reason':'Confirmed source resolution only.','resolveCorrections':True,'sourceResolutionConfirmed':True}
    assert c.client.post(base+'/review',headers=c.headers,json=payload).status_code==409


def test_password_is_consumed_and_encrypted_preview_is_private(context):
    c=context
    source=fitz.open(stream=example_pdf(rows=[]),filetype='pdf')
    raw=source.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='synthetic-password')
    doc=upload_and_process(c,raw,password='synthetic-password')
    assert doc['_id'] not in docs._PDF_PASSWORDS
    assert doc['abaStatementAnalysis']['reconciliationStatus']=='RECONCILED'
    assert c.client.get(f'/api/bank-statements/documents/{doc["_id"]}/aba/page/1',headers=c.headers).status_code==200
    assert 'synthetic-password' not in repr(c.db['documents'].rows)


def test_missing_password_can_be_retried_without_reuploading(context):
    c=context
    source=fitz.open(stream=example_pdf(rows=[]),filetype='pdf')
    raw=source.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='synthetic-password')
    doc=upload_and_process(c,raw)
    assert doc['status']=='FAILED' and doc['ocrStatus']=='OCR_FAILED'
    assert doc['abaStatementAnalysis']['statementStrengthScore'] is None
    response=c.client.post(f'/api/documents/{doc["_id"]}/ocr/retry',headers=c.headers,json={'pdfPassword':'synthetic-password'})
    assert response.status_code==200,response.text
    retry,attempt=c.scheduled[-1]
    asyncio.run(docs._process_document_ocr(retry,c.db,attempt))
    fresh=asyncio.run(c.db['documents'].find_one({'_id':doc['_id']}))
    assert fresh['ocrStatus']=='OCR_COMPLETED'
    assert fresh['abaStatementAnalysis']['statementStrengthScore']==5.5
    assert doc['_id'] not in docs._PDF_PASSWORDS


def test_late_job_does_not_resurrect_deleted_document(context):
    c=context;doc=upload_and_process(c)
    row=c.db['documents'].rows[0];row['status']='DELETED';row['ocrLeaseOwner']='new-worker'
    current=deepcopy(row)
    analysis=analyze_aba_pdf(example_pdf(rows=[]),'Synthetic.pdf')
    asyncio.run(docs._complete_local_aba(doc,c.db,analysis,'new-hash','old-worker'))
    assert row==current


def test_saved_snapshot_controls_upload_after_live_requirement_changes(context):
    c=context
    c.db['visarequirements'].rows[0].update(acceptedFormats=['png'],maxFileSize=1,name='Changed live requirement')
    doc=upload_and_process(c)
    assert doc['requirementCode']=='bank_statement'
    assert doc['abaStatementAnalysis']['statementStrengthScore']==5.5
