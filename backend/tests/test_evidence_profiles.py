from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import json
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from backend.app.services.evidence_scoring import calculate_evidence_assessment, normalize_scoring_fields, validate_profile_requirements
from backend.app.services.policy_profiles import profile_snapshot, resolve_application_requirements, application_profile_fields
from backend.app.api import policy_profiles as api
from backend.app.core.errors import APIError



def assess(requirements, documents, **kwargs):
    """A case fixture explicitly selects its sole reviewed bank; production never auto-selects."""
    if "application" not in kwargs:
        bank_docs = [d for d in documents if d.get("abaStatementAnalysis")]
        if len(bank_docs) == 1:
            doc = bank_docs[0]
            kwargs["application"] = {"selectedBankDocumentId": doc["_id"], "selectedBankAnalysisRevision": doc["abaStatementAnalysis"].get("analysisRevision")}
    return calculate_evidence_assessment(requirements, documents, **kwargs)


def requirement(identity, points, **extra):
    return {"requirementId":identity,"name":identity,"code":identity,"required":True,"weight":points,**extra}


def document(identity, **extra):
    return {"_id":identity+"-file","requirementId":identity,"applicationId":"application","status":"VERIFIED","ocrStatus":"OCR_COMPLETED","ocrTotalPages":1,"ocrPagesProcessed":1,"ocrProgressPercent":100,"ocrPageNumbers":[1],**extra}


def bank(score=7.0, **extra):
    analysis={"statementStrengthScore":score,"reconciliationStatus":"RECONCILED","reviewStatus":"VERIFIED","ownerRelationshipConfirmed":True,"analysisRevision":"bank-r1","modelVersion":"1.0","metrics":{"days":90},"statement":{"end":"2026-08-31"}}
    analysis.update(extra)
    return document("bank",abaStatementAnalysis=analysis)


def bank_profile(minimum=0):
    return [requirement("fixed",70),requirement("bank",30,scoringMode="DYNAMIC",minimumEarnedPoints=str(minimum),maximumEarnedPoints="30",qualityScorer="ABA_STATEMENT_V1")]


@pytest.mark.parametrize("score,expected",[(1,0),(4.2,10.67),(5.5,15),(7,20),(10,30)])
def test_bank_mapping_uses_final_score(score,expected):
    result=assess(bank_profile(),[bank(score)])
    assert result["requirementScores"][1]["earnedPoints"]==expected
    assert result["maximumPoints"]==100
    assert result["approvalProbability"] is None


def test_minimum_not_awarded_for_unknown_or_unverified():
    profile=bank_profile(5)
    assert assess(profile,[bank(7)])["earnedPoints"]==21.67
    assert assess(profile,[])["earnedPoints"]==0
    assert assess(profile,[bank(None)])["earnedPoints"]==0
    assert assess(profile,[bank(7,reviewStatus="PENDING_REVIEW")])["earnedPoints"]==0
    assert assess(profile,[bank(7,reconciliationStatus="NEEDS_REVIEW")])["earnedPoints"]==0
    assert assess(profile,[bank(7,ownerRelationshipConfirmed=False)])["earnedPoints"]==21.67
    score=next(item for item in assess(profile,[bank(7,ownerRelationshipConfirmed=False)])["requirementScores"] if item["requirementId"]=="bank")
    assert score["earnedPoints"]==21.67


def test_confirmed_verification_is_distinct_from_completeness():
    profile=[requirement("fixed",100)]
    doc=document("fixed",status="NEEDS_REVIEW",awardedPoints=100)
    result=assess(profile,[doc])
    assert result["submissionCompleteness"]==100
    assert result["verificationPercentage"]==0
    assert result["earnedPoints"]==0
    assert result["complete"] is False


def test_verified_photo_in_published_profile_does_not_require_ocr():
    profile = [requirement("photo", 100, code="photo", name="Applicant photo")]
    photo = document(
        "photo",
        requirementCode="photo",
        status="VERIFIED",
        ocrStatus="OCR_FAILED",
    )
    result = assess(profile, [photo])
    assert result["earnedPoints"] == 100
    assert result["verifiedRequiredDocuments"] == 1
    assert result["complete"] is True


def test_optional_budget_stable_on_upload_and_deletion():
    profile=[requirement("fixed",70),requirement("optional",30,required=False)]
    base=document("fixed")
    for docs in [[base],[base,document("optional",status="REJECTED")],[base,document("optional",status="DELETED")]]:
        result=assess(profile,docs)
        assert result["maximumPoints"]==100
        assert result["earnedPoints"]==70
        assert result["complete"] is True


def test_alternatives_share_slot_and_multiple_uploads_need_primary():
    profile=[requirement("fixed",80),requirement("employment",20,alternativeGroupId="work"),requirement("business",20,alternativeGroupId="work")]
    docs=[document("fixed"),document("employment"),document("business")]
    result=assess(profile,docs)
    assert result["maximumPoints"]==100
    assert result["earnedPoints"]==80
    assert result["requirementScores"][1]["quality"] is None
    selected=assess(profile,docs,application={"primaryDocumentSelections":{"work":"business-file"}})
    assert selected["earnedPoints"]==100
    assert len(selected["requirementScores"])==2


def test_applicability_never_shrinks_denominator():
    profile=[requirement("fixed",80),requirement("employed",20,alternativeGroupId="work",applicabilityRule={"field":"work","operator":"eq","value":"employed"}),requirement("business",20,alternativeGroupId="work",applicabilityRule={"field":"work","operator":"eq","value":"business"})]
    docs=[document("fixed"),document("employed")]
    assert assess(profile,docs)["complete"] is False
    result=assess(profile,docs,application={"applicantContext":{"work":"employed"}})
    assert result["earnedPoints"]==100 and result["complete"]
    assert assess(profile,docs,application={"applicantContext":{"work":"student"}})["maximumPoints"]==100


def test_manual_rubric_is_versioned_bounded_and_reviewed():
    profile=[requirement("fixed",80),requirement("work",20,scoringMode="DYNAMIC",qualityScorer="MANUAL_RUBRIC",qualityRubric={"version":"1","criteria":[{"id":"continuity","maximumPoints":"10"},{"id":"income","maximumPoints":"10"}]})]
    doc=document("work",qualityAssessment={"rubricVersion":"1","reviewedBy":"staff","reason":"Compared the source evidence","criterionScores":{"continuity":"7","income":"7"}})
    result=assess(profile,[document("fixed"),doc])
    assert result["earnedPoints"]==94
    doc["qualityAssessment"]["rubricVersion"]="old"
    assert assess(profile,[document("fixed"),doc])["earnedPoints"]==80
    doc["qualityAssessment"]["rubricVersion"]="1";doc["qualityAssessment"]["criterionScores"]["income"]="11"
    assert assess(profile,[document("fixed"),doc])["earnedPoints"]==80


def test_aggregate_rounding_idempotency_and_fingerprint_invalidation():
    profile=bank_profile()
    docs=[document("fixed"),bank(4.2)]
    result=assess(profile,docs)
    assert result["earnedPoints"]==80.67
    assert Decimal(result["exactPoints"])>Decimal("80.6666")
    again=assess(profile,docs)
    assert result["inputFingerprint"]==again["inputFingerprint"]
    docs[1]["abaStatementAnalysis"]["analysisRevision"]="r2"
    assert assess(profile,docs)["inputFingerprint"]!=result["inputFingerprint"]


@pytest.mark.parametrize("bad",["NaN","Infinity",-1,0,True])
def test_invalid_allocations_rejected(bad):
    with pytest.raises(ValueError):normalize_scoring_fields({"weight":bad})


def test_budget_alias_official_sources_and_branches_validated():
    assert validate_profile_requirements([requirement("a",99)])[0]["weight"] == 99
    with pytest.raises(ValueError,match="alias"):normalize_scoring_fields({"weight":10,"maximumEarnedPoints":20})
    with pytest.raises(ValueError,match="source"):validate_profile_requirements([requirement("a",100,sourceKind="OFFICIAL")])
    with pytest.raises(ValueError,match="alternative"):validate_profile_requirements([requirement("a",100,applicabilityRule={"field":"work","value":"employee"})])
    invalid=assess([requirement("a",99)],[])
    assert invalid["configurationValid"] and invalid["maximumPoints"] == 99
    assert invalid["approvalProbability"] is None


class Cursor:
    def __init__(self,rows):self.rows=rows
    async def to_list(self,length=None):return deepcopy(self.rows)


class Collection:
    def __init__(self):self.rows=[]
    def find(self,query):return Cursor([r for r in self.rows if self.matches(r,query)])
    @staticmethod
    def matches(row,query):
        return all((row.get(key) in value['$in'] if isinstance(value,dict) and '$in' in value else row.get(key)==value) for key,value in query.items())
    async def find_one(self,query):return deepcopy(next((r for r in self.rows if self.matches(r,query)),None))
    async def insert_one(self,row):
        if any(r['_id']==row['_id'] for r in self.rows):raise DuplicateKeyError('duplicate')
        self.rows.append(deepcopy(row));return SimpleNamespace(inserted_id=row['_id'])
    async def replace_one(self,query,row):
        for i,current in enumerate(self.rows):
            if self.matches(current,query):self.rows[i]=deepcopy(row);return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)
    async def update_one(self,query,update):
        for current in self.rows:
            if self.matches(current,query):
                current.update(deepcopy(update.get('$set',{})))
                for key,val in update.get('$push',{}).items():current.setdefault(key,[]).append(deepcopy(val))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class Database(dict):
    def __getitem__(self,key):
        if key not in self:self[key]=Collection()
        return dict.__getitem__(self,key)


def test_profile_publication_atomic_snapshot_immutable_and_reassessment():
    async def scenario():
        db=Database();await db['visatypes'].insert_one({'_id':'visa','countryId':'country'})
        draft_resp=await api.create_profile({'name':'Internal test','visaTypeId':'visa','requirements':bank_profile(),'effectiveFrom':'2026-01-01'},db,{'id':'admin'})
        draft=json.loads(draft_resp.body)['profile']
        published_resp=await api.publish_profile(draft['_id'],{'revision':1,'reason':'Reviewed internal evidence policy'},db,{'id':'admin'})
        published=json.loads(published_resp.body)['profile']
        assert published['status']=='PUBLISHED'
        with pytest.raises(APIError):await api.edit_profile(published['_id'],{'revision':1,'name':'mutate'},db,{'id':'admin'})
        with pytest.raises(APIError):await api.publish_profile(draft['_id'],{'revision':1,'reason':'Repeat publication'},db,{'id':'admin'})
        fields=await application_profile_fields(db,'visa')
        assert fields['scoringVersion']=='evidence-v3'
        original=deepcopy(fields['evidencePolicySnapshot'])
        await api.edit_profile(draft['_id'],{'revision':2,'requirements':[requirement('new',100)]},db,{'id':'admin'})
        assert fields['evidencePolicySnapshot']==original
        assert resolve_application_requirements(fields,[requirement('different',100)])==original['requirements']
        await db['visaapplications'].insert_one({'_id':'application','visaTypeId':'visa','approvalProbability':.8,'selectedBankDocumentId':'bank-file','selectedBankAnalysisRevision':'bank-r1'})
        await db['documents'].insert_one(bank(7));await db['documents'].insert_one(document('fixed'))
        result=await api.reassess_application('application',{'policyVersionId':published['_id'],'reason':'Use published policy explicitly'},db,{'id':'staff'})
        saved=await db['visaapplications'].find_one({'_id':'application'})
        assert saved['predictionStatus']=='STALE' and saved['approvalProbability'] is None
        assert result['assessment']['earnedPoints']==90
        assert len(saved['assessmentHistory'])==1
    asyncio.run(scenario())


def test_dynamic_budget_profile_publishes_and_future_release_is_not_selected():
    async def scenario():
        db=Database();await db['visatypes'].insert_one({'_id':'visa','countryId':'country'})
        response=await api.create_profile({'name':'Dynamic budget','visaTypeId':'visa','requirements':[requirement('a',99)]},db,{'id':'admin'})
        draft=json.loads(response.body)['profile']
        published=await api.publish_profile(draft['_id'],{'revision':1,'reason':'Publish dynamic budget'},db,{'id':'admin'})
        published_profile=json.loads(published.body)['profile']
        assert published_profile['requirements'][0]['weight'] == 99
        assert len(db['evidencepolicyprofiles'].rows)==2
        assert (await application_profile_fields(db,'visa'))['policyVersionId']==published_profile['_id']
        response=await api.create_profile({'name':'Future','visaTypeId':'visa','requirements':bank_profile(),'version':'2','effectiveFrom':'2099-01-01'},db,{'id':'admin'})
        future=json.loads(response.body)['profile']
        await api.publish_profile(future['_id'],{'revision':1,'reason':'Schedule future release'},db,{'id':'admin'})
        assert (await application_profile_fields(db,'visa'))['policyVersionId']==published_profile['_id']
    asyncio.run(scenario())


def test_bank_requires_explicit_current_primary_selection_and_matching_model():
    profile = bank_profile()
    doc = bank(7)
    assert calculate_evidence_assessment(profile, [doc])["earnedPoints"] == 0
    app = {"selectedBankDocumentId": doc["_id"], "selectedBankAnalysisRevision": "old-revision"}
    assert calculate_evidence_assessment(profile, [doc], application=app)["earnedPoints"] == 0
    app["selectedBankAnalysisRevision"] = "bank-r1"
    assert calculate_evidence_assessment(profile, [doc], application=app)["earnedPoints"] == 20
    doc["abaStatementAnalysis"]["modelVersion"] = "custom-2"
    assert calculate_evidence_assessment(profile, [doc], application=app)["earnedPoints"] == 0


@pytest.mark.parametrize("rubric", [
    {"version":"1", "criteria":[None]},
    {"version":"1", "levels":[{"quality":".5"}]},
    {"version":"1", "levels":[{"id":"x","quality":".5"},{"id":"x","quality":"1"}]},
    {"version":"1", "criteria":[{"id":"x","maximumPoints":1}], "levels":[{"id":"x","quality":1}]},
])
def test_malformed_manual_rubrics_are_rejected_as_validation_errors(rubric):
    with pytest.raises(ValueError):
        validate_profile_requirements([requirement("a",100,scoringMode="DYNAMIC",qualityScorer="MANUAL_RUBRIC",qualityRubric=rubric)])


def test_official_source_requires_valid_url_and_past_review_date():
    official=requirement("a",100,sourceKind="OFFICIAL",sourceStatus="VERIFIED",source="javascript:alert(1)",sourceReviewedBy="admin",sourceReviewedAt="2026-01-01")
    with pytest.raises(ValueError,match="HTTP"):
        validate_profile_requirements([official])
    official.update(source="https://example.gov/visa",sourceReviewedAt="2099-01-01")
    with pytest.raises(ValueError,match="past"):
        validate_profile_requirements([official])
    official["sourceReviewedAt"]="2026-01-01"
    assert validate_profile_requirements([official])[0]["sourceStatus"]=="VERIFIED"


def test_profile_effective_date_uses_configured_local_timezone():
    from backend.app.services.policy_profiles import load_published_profile
    async def scenario():
        db=Database()
        await db["evidencepolicyprofiles"].insert_one({"_id":"p","visaTypeId":"visa","status":"PUBLISHED","timezone":"Asia/Phnom_Penh","effectiveFrom":"2026-01-02","publishedAt":datetime(2026,1,1,tzinfo=timezone.utc)})
        assert await load_published_profile(db,"visa",as_of=datetime(2026,1,1,16,59,tzinfo=timezone.utc)) is None
        assert await load_published_profile(db,"visa",as_of=datetime(2026,1,1,17,0,tzinfo=timezone.utc)) is not None
    asyncio.run(scenario())


def test_manual_review_api_validates_version_and_does_not_verify_document():
    async def scenario():
        db=Database()
        req=requirement("work",100,scoringMode="DYNAMIC",qualityScorer="MANUAL_RUBRIC",qualityRubric={"version":"1","levels":[{"id":"strong","quality":"1"}]})
        await db["visaapplications"].insert_one({"_id":"application","evidencePolicySnapshot":{"id":"p","scoringVersion":"evidence-v3","requirements":[req]}})
        await db["documents"].insert_one(document("work",status="NEEDS_REVIEW"))
        with pytest.raises(APIError):
            await api.review_requirement("application",{"slotId":"work","documentId":"work-file","reason":"Reviewed evidence","levelId":"unknown"},db,{"id":"staff"})
        result=await api.review_requirement("application",{"slotId":"work","documentId":"work-file","reason":"Reviewed evidence","levelId":"strong"},db,{"id":"staff"})
        assert result["assessment"]["earnedPoints"]==0
        assert result["document"]["status"]=="NEEDS_REVIEW"
        assert result["document"]["qualityAssessment"]["rubricVersion"]=="1"
    asyncio.run(scenario())


def test_profile_routes_enforce_admin_publication_and_staff_read_scope():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from backend.app.api.dependencies import get_current_principal, get_mongo_database
    app=FastAPI()
    db=Database()
    actor={"id":"actor","role":"CUSTOMER","email":"actor@example.invalid"}
    app.include_router(api.router,prefix="/api/policy-profiles")
    app.dependency_overrides[get_current_principal]=lambda:actor
    app.dependency_overrides[get_mongo_database]=lambda:db
    @app.exception_handler(APIError)
    async def error_handler(_request,error):
        return JSONResponse(status_code=error.status_code,content={"message":error.message})
    with TestClient(app) as client:
        assert client.get("/api/policy-profiles").status_code==403
        actor["role"]="STAFF"
        assert client.get("/api/policy-profiles").status_code==200
        assert client.post("/api/policy-profiles",json={"name":"Forbidden","visaTypeId":"visa"}).status_code==403
        assert client.post("/api/policy-profiles/p/publish",json={"revision":1,"reason":"Forbidden"}).status_code==403
    assert not db["evidencepolicyprofiles"].rows


def test_effective_profile_read_exposes_only_current_published_snapshot_to_customer():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from backend.app.api.dependencies import get_current_principal, get_mongo_database
    from backend.app.services.policy_profiles import content_hash
    db=Database()
    async def seed():
        await db["visatypes"].insert_one({"_id":"visa","active":True})
        base={"name":"Current","version":"1","visaTypeId":"visa","requirements":bank_profile(),"scoringVersion":"evidence-v3","timezone":"Asia/Phnom_Penh","publishedAt":datetime(2026,1,1,tzinfo=timezone.utc)}
        await db["evidencepolicyprofiles"].insert_one({**base,"_id":"current","status":"PUBLISHED","effectiveFrom":"2026-01-01"})
        await db["evidencepolicyprofiles"].insert_one({**base,"_id":"future","version":"2","status":"PUBLISHED","effectiveFrom":"2099-01-01"})
        await db["evidencepolicyprofiles"].insert_one({**base,"_id":"draft","version":"3","status":"DRAFT"})
        await db["evidencepolicyprofiles"].insert_one({**base,"_id":"expired","version":"4","status":"PUBLISHED","effectiveFrom":"2000-01-01","effectiveUntil":"2001-01-01"})
    asyncio.run(seed())
    app=FastAPI()
    app.include_router(api.router,prefix="/api/policy-profiles")
    app.dependency_overrides[get_current_principal]=lambda:{"id":"customer","role":"CUSTOMER","email":"customer@example.invalid"}
    app.dependency_overrides[get_mongo_database]=lambda:db
    @app.exception_handler(APIError)
    async def error_handler(_request,error):
        return JSONResponse(status_code=error.status_code,content={"message":error.message})
    with TestClient(app) as client:
        result=client.get("/api/policy-profiles/effective/visa-type/visa")
        assert result.status_code==200
        profile=result.json()["profile"]
        assert profile["id"]=="current"
        assert profile["requirements"]==bank_profile()
        assert profile["status"]=="PUBLISHED"
        assert client.get("/api/policy-profiles").status_code==403
        assert client.get("/api/policy-profiles/future").status_code==403
        db["visatypes"].rows[0]["active"]=False
        assert client.get("/api/policy-profiles/effective/visa-type/visa").json()=={"profile":None}


def test_application_creation_uses_latest_requirements_not_policy_profiles():
    from backend.app.api.visa_types import create_application
    async def scenario():
        db=Database()
        await db["customers"].insert_one({"_id":"customer","fullName":"Synthetic applicant"})
        await db["visatypes"].insert_one({"_id":"visa","countryId":"country","active":True,"name":"Visitor"})
        await db["visarequirements"].insert_one({"_id":"changed","visaTypeId":"visa","status":"ACTIVE","weight":1,"name":"Changed live row"})
        first=json.loads((await create_application({"customer":"customer","visaTypeId":"visa"},db)).body)["application"]
        assert first["requirementSnapshot"][0]["requirementId"]=="changed"
        assert first["requirementSnapshot"][0]["weight"]==1
        assert "evidencePolicySnapshot" not in first
        assert "policyVersionId" not in first
        db["visarequirements"].rows[0]["weight"]=2
        second=json.loads((await create_application({"customer":"customer","visaTypeId":"visa"},db)).body)["application"]
        assert second["requirementSnapshot"][0]["weight"]==2
        assert first["requirementSnapshot"][0]["weight"]==1
    asyncio.run(scenario())
