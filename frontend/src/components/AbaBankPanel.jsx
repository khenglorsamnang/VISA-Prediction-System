import { useEffect, useRef, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ChevronLeft, ChevronRight, Search, ShieldAlert } from 'lucide-react';
import { api } from '../pages/pageUtils';
import './Integration.css';

const money = (value, currency='USD') => value == null ? '—' : new Intl.NumberFormat('en-US',{style:'currency',currency}).format(Number(value)/100);
const chartAmount = value => {
  const amount = Number(value) / 100;
  return Math.abs(amount) >= 1000
    ? `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(amount / 1000)}k`
    : new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(amount);
};
const majorAmount = (value, currency) => value == null
  ? '—'
  : new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(Number(value));
const SOURCE_CATEGORIES = [
  ['SALARY', 'Salary / ប្រាក់ខែ'],
  ['BUSINESS_INCOME', 'Business Income / ចំណូលអាជីវកម្ម'],
  ['FAMILY_SUPPORT', 'Family Support / គ្រួសារជួយ'],
  ['PERSONAL_SAVINGS', 'Personal Savings / ប្រាក់សន្សំ'],
  ['OTHER', 'Other / ផ្សេងៗ'],
  ['UNKNOWN', 'Unknown / មិនដឹងប្រភពច្បាស់លាស់'],
];
const transactionReceiptDetails = transaction => {
  const description=String(transaction.description||'').trim();
  const title=(description.split(/\s+ORIGINAL AMOUNT\b/i)[0]||description)
    .replace(/^FUNDS\s+TRANSFERRED\b/i,'TRANSFER')
    .trim();
  const dateMatch=description.match(/\bON\s+([\s\S]*?)(?=\s+REMARK\s*:|$)/i);
  return {
    title,
    transactionDate:dateMatch?.[1]?.trim()||transaction.date||'Unavailable',
  };
};
export default function AbaBankPanel({ documentId, canManage, onChange }) {
  const [data,setData]=useState(null); const [selectedPrimary,setSelectedPrimary]=useState(false);
  const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
  const [query,setQuery]=useState('');const [page,setPage]=useState(0);
  const [sourceInputs,setSourceInputs]=useState({});
  const [sourceReviewOpen,setSourceReviewOpen]=useState({});
  const [expandedReviewRows,setExpandedReviewRows]=useState({});
  const [sourceReviewResults,setSourceReviewResults]=useState({});
  const sourceReviewSaving=useRef(false);
  const base=`/bank-statements/documents/${documentId}/aba`;
  function applyResponse(response){
    setData({
      ...response.analysis,
      staffReviewStatus:response.staffReviewStatus??response.analysis?.staffReviewStatus,
      documentReviewStatus:response.documentReviewStatus??response.analysis?.documentReviewStatus,
      financialEvidenceStatus:response.financialEvidenceStatus??response.analysis?.financialEvidenceStatus,
      confirmedBankScore:response.confirmedBankScore,
      bankStatementAnalysis:response.bankStatementAnalysis,
      verificationBlockers:response.verificationBlockers||[],
      reviewerId:response.reviewerId,
      reviewedAt:response.reviewedAt,
      reviewNote:response.reviewNote,
      manualReviewHistory:response.manualReviewHistory||[],
    });
    setSelectedPrimary(Boolean(response.selectedPrimary));
  }
  async function load(){const r=await api.get(base);applyResponse(r.data);}
  useEffect(()=>{let active=true;setError('');api.get(base).then(r=>{if(active)applyResponse(r.data);}).catch(e=>{if(active)setError(e.response?.data?.message||'ABA analysis is not yet available. Complete document processing or retry.');});return()=>{active=false;};},[base]);
  useEffect(()=>{setPage(0);setSourceReviewOpen({});setExpandedReviewRows({});},[documentId]);
  useEffect(()=>{setPage(0);},[query]);
  const metrics=data?.metrics||{};const currency=data?.source?.currency||data?.statement?.currency||'USD';
  const rows=data?.transactions||[];const daily=data?.daily||[];
  const bankScore=data?.bankStatementAnalysis?.bankStatementScore||{};
  const computedBankScore=data?.bankStatementAnalysis?.computedBankScore||{};
  const bankScoreCategories=[
    ['ending_balance','Ending Balance'],
    ['starting_balance','Starting Balance'],
    ['transaction_history','Transaction History'],
    ['transaction_period','Transaction Period'],
  ];
  const reviewRequirements=bankScore.transactionReviewRequirements||{};
  const componentMaximum=Object.values(bankScore.components||{}).reduce((sum,item)=>sum+(Number.isFinite(Number(item.maximum))?Number(item.maximum):0),0);
  const bankScoreMaximum=computedBankScore.maximum??bankScore.maximum??reviewRequirements.maximumBankScore??(componentMaximum||null);
  const deductionPoints=reviewRequirements.unverifiedSourcePenaltyPoints??0.5;
  const exchangeRatesToUsd=reviewRequirements.exchangeRatesToUsd||{USD:1};
  const moneyInThresholdUsd=Number(reviewRequirements.moneyInFlagThresholdUsd??2000);
  const moneyOutThresholdUsd=Number(reviewRequirements.moneyOutFlagThresholdUsd??1000);
  const minimumBalanceThresholdUsd=Number(reviewRequirements.minimumMonthlyClosingBalanceUsd??4000);
  const deductionText=typeof deductionPoints==='number'&&Number.isFinite(deductionPoints)&&typeof bankScoreMaximum==='number'&&Number.isFinite(bankScoreMaximum)&&bankScoreMaximum>0
    ?`−${deductionPoints.toFixed(2)} points (−${(deductionPoints/bankScoreMaximum*100).toFixed(2)}%)`
    :null;
  const reviewableTransactions=(bankScore.transactionReviews||[]).filter(transaction=>transaction.requiresStaffReview===true);
  const reviewedTransactionCount=reviewableTransactions.filter(transaction=>{
    const status=String(sourceReviewResults[transaction.transactionIndex]?.status||transaction.status||'PENDING_REVIEW').toUpperCase();
    return status==='CONFIRMED'||status==='UNVERIFIED';
  }).length;
  const pendingTransactionCount=Math.max(0,reviewableTransactions.length-reviewedTransactionCount);
  const verificationBlockers=data?.verificationBlockers||[];
  const otherVerificationBlockers=[...new Set(verificationBlockers.filter(blocker=>
    !String(blocker).toLowerCase().includes('source review is unresolved')
  ))];
  const thresholdFlagsForReview=(transaction)=>{
    const flags=[];
    const amountUsd=Number(transaction.amountUsd);
    if(transaction.direction==='credit'&&Number.isFinite(amountUsd)&&amountUsd>=moneyInThresholdUsd){
      flags.push({flagType:'MONEY_IN_THRESHOLD',amountUsd,thresholdUsd:moneyInThresholdUsd});
    }
    if(transaction.direction==='debit'&&Number.isFinite(amountUsd)&&amountUsd>=moneyOutThresholdUsd){
      flags.push({flagType:'MONEY_OUT_THRESHOLD',amountUsd,thresholdUsd:moneyOutThresholdUsd});
    }
    const balanceCurrency=String(transaction.balanceCurrency||transaction.currency||currency).toUpperCase();
    const balanceRate=balanceCurrency==='USD'?1:Number(exchangeRatesToUsd[balanceCurrency]);
    const balanceUsd=transaction.balanceUsd!=null&&Number.isFinite(Number(transaction.balanceUsd))
      ?Number(transaction.balanceUsd)
      :transaction.balance!=null&&String(transaction.balance).trim()!==''&&Number.isFinite(Number(transaction.balance))&&Number.isFinite(balanceRate)
        ?Number(transaction.balance)*balanceRate
        :null;
    if(balanceUsd!=null&&balanceUsd<minimumBalanceThresholdUsd){
      flags.push({flagType:'TRANSACTION_BALANCE_BELOW_MINIMUM',amountUsd:balanceUsd,thresholdUsd:minimumBalanceThresholdUsd});
    }
    return flags;
  };
  const flaggedReviewableTransactions=reviewableTransactions.filter(
    transaction=>thresholdFlagsForReview(transaction).length>0,
  );
  const filtered=rows.filter(t=>`${t.date} ${t.description} ${t.reference||''}`.toLowerCase().includes(query.toLowerCase()));
  const thresholdFlagsForRow=(transaction)=>{
    const transactionCurrency=String(transaction.currency||currency).toUpperCase();
    const exchangeRate=transactionCurrency==='USD'?1:Number(exchangeRatesToUsd[transactionCurrency]);
    const amountToUsd=(minorAmount)=>minorAmount==null||String(minorAmount).trim()===''||!Number.isFinite(Number(minorAmount))||!Number.isFinite(exchangeRate)
      ?null
      :Number(minorAmount)/100*exchangeRate;
    const moneyInUsd=amountToUsd(transaction.money_in);
    const moneyOutUsd=amountToUsd(transaction.money_out);
    const balanceUsd=amountToUsd(transaction.balance);
    const flags=[];
    if(moneyInUsd!=null&&moneyInUsd>=moneyInThresholdUsd)flags.push({type:'MONEY_IN_THRESHOLD',label:`Money In flag · ${majorAmount(moneyInUsd,'USD')} ≥ ${majorAmount(moneyInThresholdUsd,'USD')}`});
    if(moneyOutUsd!=null&&moneyOutUsd>=moneyOutThresholdUsd)flags.push({type:'MONEY_OUT_THRESHOLD',label:`Money Out flag · ${majorAmount(moneyOutUsd,'USD')} ≥ ${majorAmount(moneyOutThresholdUsd,'USD')}`});
    if(balanceUsd!=null&&balanceUsd<minimumBalanceThresholdUsd)flags.push({type:'TRANSACTION_BALANCE_BELOW_MINIMUM',label:`Balance below minimum · ${majorAmount(balanceUsd,'USD')} < ${majorAmount(minimumBalanceThresholdUsd,'USD')}`});
    return flags;
  };
  const reconciled=data?.reconciliationStatus==='RECONCILED';
  async function reviewSource(transaction,action){
    if(sourceReviewSaving.current)return;
    const index=transaction.transactionIndex;
    const input=sourceInputs[index]||{};
    const note=(input.notes??transaction.reviewNotes??'').trim();
    const sourceCategory=input.sourceCategory??transaction.sourceCategory??'';
    if(!sourceCategory){setError('Choose a source category before saving either decision.');return;}
    if(action==='CONFIRM_SOURCE'&&sourceCategory==='UNKNOWN'){setError('Unknown source can only be marked unverified.');return;}
    if(sourceCategory==='OTHER'&&!note){setError('A Staff Review Note is required when “Other / ផ្សេងៗ” is selected.');return;}
    if(action==='SOURCE_UNVERIFIED'&&!deductionText){setError('The source-review deduction is unavailable.');return;}
    sourceReviewSaving.current=true;
    setBusy(true);setError('');setNotice('');
    try{
      const response=await api.post(`/bank-statements/documents/${documentId}/transactions/${index}/source-review`,{action,sourceCategory,notes:note,requestedEvidence:''});
      if(response.data?.bankStatementAnalysis){
        setData(current=>current?{...current,bankStatementAnalysis:response.data.bankStatementAnalysis}:current);
      }
      const audit=response.data?.audit||{};
      const status=audit.status||(action==='CONFIRM_SOURCE'?'CONFIRMED':'UNVERIFIED');
      const auditHistory=(Array.isArray(response.data?.auditHistory)?response.data.auditHistory:[]).filter(item=>item.transactionIndex===index);
      if(!auditHistory.length&&audit.status)auditHistory.push(audit);
      const sourceCategoryLabel=audit.sourceCategoryLabel||SOURCE_CATEGORIES.find(([value])=>value===sourceCategory)?.[1];
      setSourceReviewResults(current=>({...current,[index]:{status,sourceCategory:audit.sourceCategory||sourceCategory,sourceCategoryLabel,reviewerId:audit.reviewerId,reviewedAt:audit.reviewedAt,notes:audit.notes??note,reviewHistory:auditHistory}}));
      setSourceInputs(current=>({...current,[index]:{...current[index],sourceCategory:audit.sourceCategory||sourceCategory,notes:audit.notes??note}}));
      setSourceReviewOpen(current=>({...current,[index]:false}));
      setExpandedReviewRows(current=>({...current,[index]:false}));
      setNotice('Source review saved.');
      try{await load();await onChange?.();}catch(refreshError){setError(`The decision was saved, but the latest review could not be refreshed: ${refreshError.response?.data?.message||refreshError.message}`);}
    }catch(e){setError(e.response?.data?.message||e.message);}finally{sourceReviewSaving.current=false;setBusy(false);}
  }
  function updateSourceInput(index,key,next){setSourceInputs(current=>({...current,[index]:{...current[index],[key]:next}}));}
  if(!data)return <section className="integration-panel"><h2>ABA statement reader</h2><p className="integration-note">{error||'Loading the local statement analysis…'}</p></section>;
  return <section className="integration-panel" aria-label="ABA statement strength and source review">
    <div className="integration-heading">
      <div>
        <h2>Bank statement review</h2>
        <p>{data.source?.start||data.statement?.start} — {data.source?.end||data.statement?.end} · {metrics.days||'—'} days</p>
      </div>
      <span className="integration-tag">{selectedPrimary?'Primary statement':data.reviewStatus}</span>
    </div>
    {error&&<p role="alert" className="integration-error">{error}</p>}
    {notice&&<p role="status" className="integration-success">{notice}</p>}
    <div className="integration-status-strip integration-status-strip-compact" aria-label="Review status">
      <div><small>Financial evidence</small><strong>{data.financialEvidenceStatus||'NEEDS_REVIEW'}</strong></div>
      <div>
        <small>Staff review</small>
        <strong>{data.staffReviewStatus||'PENDING_REVIEW'}</strong>
        {data.staffReviewStatus==='VERIFIED'&&data.reviewedAt&&<small className="mt-1 block">Completed {new Date(data.reviewedAt).toLocaleString()}</small>}
      </div>
      <div><small>Source reviews</small><strong>{reviewedTransactionCount} reviewed · {pendingTransactionCount} pending</strong></div>
      <div><small>Authenticity</small><strong>{data.authenticityStatus||data.bankStatementAnalysis?.authenticityStatus||'NOT_VERIFIED'}</strong></div>
    </div>
    {data.reviewBlockers?.length>0&&<details className="integration-warning" role="status">
      <summary>{data.reviewBlockers.length} unresolved evidence item{data.reviewBlockers.length===1?'':'s'}</summary>
      {data.reviewBlockers.map((item,index)=><div key={`${item.code}-${index}`}><p>{item.message}{item.field?` · ${item.field}`:''}{item.page?` · page ${item.page}`:''}{item.pages?.length?` · pages ${item.pages.join(', ')}`:''}{item.row?` · row ${item.row}`:''}</p>{item.candidates?.length>0&&<pre className="overflow-auto text-xs">{JSON.stringify(item.candidates,null,2)}</pre>}</div>)}
    </details>}
    <section aria-label="Bank statement financial score and transaction source review">
      {otherVerificationBlockers.length>0&&<details className="integration-warning" role="status">
        <summary>{otherVerificationBlockers.length} additional verification item{otherVerificationBlockers.length===1?'':'s'}</summary>
        <ul className="mt-2 list-disc pl-5">{otherVerificationBlockers.map((blocker,index)=><li key={`${blocker}-${index}`}>{blocker}</li>)}</ul>
      </details>}
      <div className="integration-score-heading">
        <h3>Period score components</h3>
      </div>
      <div className="integration-component-grid">
        {bankScoreCategories.map(([key,label])=>{const item=bankScore.components?.[key];return <article key={key}>
          <small>{label}</small>
          <strong>{item?.points==null?`Unavailable / ${item?.maximum??'—'}`:`${Number(item.points).toFixed(2)} / ${item.maximum??'—'}`}</strong>
        </article>;})}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <article className="rounded-lg border border-slate-200 bg-white p-3">
          <small className="text-slate-500">Current calculated score</small>
          <strong className="mt-1 block text-lg text-slate-900">
            {computedBankScore?.available
              ? `${Number(computedBankScore.points).toFixed(2)} / ${computedBankScore.maximum}`
              : `Unavailable / ${computedBankScore?.maximum ?? bankScoreMaximum ?? 45}`}
          </strong>
        </article>
        <article className="rounded-lg border border-slate-200 bg-white p-3">
          <small className="text-slate-500">Confirmed score</small>
          <strong className="mt-1 block text-lg text-slate-900">
            {data.confirmedBankScore?.status === 'CONFIRMED'
              ? `${Number(data.confirmedBankScore.points).toFixed(2)} / ${data.confirmedBankScore.maximum}`
              : 'Not yet confirmed'}
          </strong>
        </article>
      </div>
      {bankScore.sourceReviewDeductionAudit && (
        <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
          Source review audit: {bankScore.sourceReviewDeductionAudit.unverifiedTransactionCount ?? 0} UNVERIFIED
          {" · "}−{Number(bankScore.sourceReviewDeductionAudit.totalDeductionPoints || 0).toFixed(2)} points
          {" · "}Transaction History {Number(bankScore.sourceReviewDeductionAudit.transactionHistoryCurrentPoints ?? bankScore.components?.transaction_history?.points ?? 0).toFixed(2)}
          / {Number(bankScore.sourceReviewDeductionAudit.transactionHistoryBasePoints ?? bankScore.components?.transaction_history?.maximum ?? 9).toFixed(2)}
        </p>
      )}
      {flaggedReviewableTransactions.length>0&&<details className="integration-review-queue">
        <summary>
          <span>Source reviews</span>
          <strong>{flaggedReviewableTransactions.length} transactions</strong>
        </summary>
        <div className="integration-review-columns" aria-hidden="true">
          <span>Transaction</span>
          <span>Money in</span>
          <span>Money out</span>
          <span>Balance</span>
          <span>Review · deduction</span>
        </div>
        <ol className="integration-review-list" aria-label="Source review transactions">
        {flaggedReviewableTransactions.map((transaction) => {
          const index = transaction.transactionIndex;
          const input = sourceInputs[index] || {};
          const savedReview = sourceReviewResults[index] || {};
          const reviewNotes = savedReview.notes ?? transaction.reviewNotes;
          const sourceCategory = input.sourceCategory ?? transaction.sourceCategory ?? '';
          const reviewNote = (input.notes ?? transaction.reviewNotes ?? '').trim();
          const noteRequired = sourceCategory === 'OTHER';
          const transactionFlags = thresholdFlagsForReview(transaction);
          const sourceDeduction = transaction.sourceDeduction || {
            points: 0,
            status: 'NO_DEDUCTION',
          };
          const reference = transaction.reference ?? transaction.referenceNumber;
          const reviewStatus =
            savedReview.status || transaction.status || 'PENDING_REVIEW';
          const appliedDeductionTotal =
            reviewStatus !== 'UNVERIFIED'
              ? 0
              : sourceDeduction.status === 'CAPPED'
                ? Math.min(deductionPoints, Math.max(0, Number(sourceDeduction.points) || 0))
                : deductionPoints;
          const reviewStatusClass = reviewStatus.toLowerCase().replaceAll('_', '-');
          const hasLowBalanceFlag = transactionFlags.some(
            (flag) => flag.flagType === 'TRANSACTION_BALANCE_BELOW_MINIMUM',
          );
          const balanceAvailable =
            transaction.balance != null
            && String(transaction.balance).trim() !== ''
            && Number.isFinite(Number(transaction.balance));
          const receipt=transactionReceiptDetails(transaction);
          const originalAmount=Number.isFinite(Number(transaction.amount))
            ?`${Number(transaction.amount).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} ${transaction.currency||currency}`
            :'Unavailable';
          return <li key={index} className="integration-review-item">
          <details
            open={Boolean(expandedReviewRows[index])}
            onToggle={(event)=>{
              const isOpen=event.currentTarget.open;
              setExpandedReviewRows(current=>current[index]===isOpen?current:{...current,[index]:isOpen});
              if(!isOpen)setSourceReviewOpen(current=>current[index]?{...current,[index]:false}:current);
            }}
          >
            <summary className="integration-review-transaction-summary">
              <span className="integration-review-transaction-description">
                <strong>{transaction.date || 'Date unavailable'}</strong>
              </span>
              <span className="integration-review-transaction-cell">
                <small>Money in</small>
                <strong>{transaction.direction === 'credit' ? majorAmount(transaction.amount, transaction.currency || currency) : majorAmount(0, transaction.currency || currency)}</strong>
              </span>
              <span className="integration-review-transaction-cell">
                <small>Money out</small>
                <strong>{transaction.direction === 'debit' ? majorAmount(transaction.amount, transaction.currency || currency) : '—'}</strong>
              </span>
              <span className="integration-review-transaction-cell">
                <small>Balance</small>
                <strong className={hasLowBalanceFlag ? 'integration-low-balance-amount' : ''}>
                  {balanceAvailable
                    ? majorAmount(transaction.balance, transaction.balanceCurrency || transaction.currency || currency)
                    : 'Balance unavailable'}
                </strong>
              </span>
              <span className="integration-review-status-cell">
                <span className={`integration-tag integration-status-${reviewStatusClass}`}>{reviewStatus}</span>
                {canManage&&<button
                  type="button"
                  className="integration-review-source-button"
                  aria-expanded={Boolean(sourceReviewOpen[index])}
                  onClick={(event)=>{
                    event.preventDefault();
                    event.stopPropagation();
                    if(sourceReviewOpen[index]){
                      setSourceReviewOpen(current=>({...current,[index]:false}));
                      setExpandedReviewRows(current=>({...current,[index]:false}));
                    }else{
                      setExpandedReviewRows(current=>({...current,[index]:true}));
                      setSourceReviewOpen(current=>({...current,[index]:true}));
                    }
                  }}
                >{sourceReviewOpen[index]?'Hide Staff Review':'Review source'}</button>}
                <small>{appliedDeductionTotal > 0 ? `−${appliedDeductionTotal.toFixed(2)} points applied` : 'No deduction'}</small>
              </span>
            </summary>
            <article className="integration-review-content">
              {!sourceReviewOpen[index]&&<>
                <dl className="integration-review-detail-grid">
                  <div><dt>Transaction description</dt><dd>{receipt.title||'Transaction'}</dd></div>
                  <div><dt>Original amount</dt><dd>{originalAmount}</dd></div>
                  <div><dt>Reference #</dt><dd>{reference||'—'}</dd></div>
                  <div><dt>Transaction date</dt><dd>{receipt.transactionDate}</dd></div>
                  <div><dt>USD equivalent</dt><dd>{majorAmount(transaction.amountUsd,'USD')} · {Number(transaction.exchangeRateToUsd).toFixed(6)} USD per {transaction.currency}</dd></div>
                </dl>
                {(savedReview.sourceCategoryLabel||transaction.sourceCategoryLabel)&&<p className="text-sm"><strong>Source category:</strong> {savedReview.sourceCategoryLabel||transaction.sourceCategoryLabel}</p>}
                {reviewNotes&&<p className="break-words text-sm"><strong>Staff Review Note:</strong> {reviewNotes}</p>}
              </>}
              {canManage&&sourceReviewOpen[transaction.transactionIndex]&&<div className="integration-source-review-form">
                <label>ប្រភពប្រាក់
                  <select required aria-required="true" value={input.sourceCategory??transaction.sourceCategory??''} onChange={event=>updateSourceInput(transaction.transactionIndex,'sourceCategory',event.target.value)}>
                    <option value="">ជ្រើសរើសប្រភពប្រាក់</option>
                    {SOURCE_CATEGORIES.map(([value,label])=><option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                {noteRequired&&<label>Staff Review Note<span className="text-red-700"> *</span>
                  <textarea aria-required={noteRequired} rows={4} value={input.notes??transaction.reviewNotes??''} onChange={event=>updateSourceInput(transaction.transactionIndex,'notes',event.target.value)} placeholder="បញ្ចូលកំណត់ចំណាំ ឬភស្តុតាងដែលបានពិនិត្យ…"/>
                </label>}
                {noteRequired&&!reviewNote&&<p role="status" className="text-sm text-red-700">A Staff Review Note is required when “Other / ផ្សេងៗ” is selected.</p>}
                {!sourceCategory&&<p className="integration-source-review-hint">ជ្រើសរើសប្រភពមុនបន្ត</p>}
                <div className="integration-actions integration-source-review-actions">
                  <div className="integration-source-review-buttons">
                    <button type="button" aria-describedby={sourceCategory==='UNKNOWN'?`unknown-source-help-${transaction.transactionIndex}`:undefined} disabled={busy||!sourceCategory||sourceCategory==='UNKNOWN'||(noteRequired&&!reviewNote)} onClick={()=>reviewSource(transaction,'CONFIRM_SOURCE')}>Confirm source</button>
                    <button type="button" disabled={busy||!sourceCategory||(noteRequired&&!reviewNote)||!deductionText} onClick={()=>reviewSource(transaction,'SOURCE_UNVERIFIED')}>Source unverified</button>
                  </div>
                  <span className="integration-source-review-deduction" aria-live="polite">{deductionText?`Source unverified deduction: ${deductionText}`:'Source-review deduction unavailable'}</span>
                </div>
                {sourceCategory==='UNKNOWN'&&<p id={`unknown-source-help-${transaction.transactionIndex}`} role="status" className="text-sm font-medium text-amber-800">ប្រភពប្រាក់មិនច្បាស់លាស់ — អាចជ្រើសរើស Source unverified បានតែប៉ុណ្ណោះ។</p>}
              </div>}
            </article>
          </details>
        </li>;})}
        </ol>
      </details>}
    </section>
    {data.score?.provisional&&<p className="integration-warning">Limited history: this reader score is provisional. Standard bank confirmation requires at least 60 complete days.</p>}
    {!reconciled&&<p className="integration-warning"><ShieldAlert size={16}/> Score withheld while financial evidence remains unresolved. Displayed metrics are unverified.</p>}
    <details className="integration-transaction-history">
      <summary>Transaction activity · {rows.length} records</summary>
    {daily.length>0&&<><div className="integration-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={daily} margin={{left:0,right:15,top:10,bottom:5}}><CartesianGrid vertical={false} stroke="#e2e8f0"/><XAxis dataKey="date" tickFormatter={d=>d.slice(5)} minTickGap={45} tickLine={false} axisLine={false}/><YAxis width={60} tickFormatter={chartAmount} tickLine={false} axisLine={false}/><ReferenceArea x1={daily[Math.max(0,daily.length-7)].date} x2={daily[daily.length-1].date} fill="#fef3c7" fillOpacity={.65}/>{currency==='USD'&&<><ReferenceLine y={400000} stroke="#94a3b8" strokeDasharray="4 4"/><ReferenceLine y={500000} stroke="#94a3b8" strokeDasharray="4 4"/></>}<Tooltip trigger="click" wrapperStyle={{pointerEvents:'auto'}} content={({active,payload,label})=>{if(!active||!payload?.length)return null;const d=payload[0].payload;const ids=d.transactions?.length?d.transactions:d.source_transaction?[d.source_transaction]:[];return <div className="integration-tooltip"><strong>{label}</strong><span>{money(d.balance,currency)}</span>{!d.transactions?.length&&<span>Carried-forward close</span>}{ids.slice(-4).map(id=>{const t=rows.find(t=>t.id===id);return t?<span key={id}>Row {id} · page {t.page}</span>:null;})}{!ids.length&&<span>Opening balance · page 1</span>}</div>;}}/><Area type="stepAfter" dataKey="balance" stroke="#2563eb" fill="#dbeafe" strokeWidth={2} isAnimationActive={false}/></AreaChart></ResponsiveContainer></div><p className="integration-note">Chart amounts are in {currency}. Source row references are shown with each transaction. The final week is shaded. USD 4,000 / 5,000 reference lines appear only for USD accounts.</p></>}
    <details><summary>Reconciliation checks and scoring evidence</summary>{(data.issues||[]).map((i,n)=><div className="integration-warning" key={n}>{i.message}{i.difference!=null&&<span> · Difference {money(i.difference,currency)}</span>}{i.row&&<span className="ml-3">Row {i.row} · page {i.page}</span>}</div>)}{!data.issues?.length&&<p className="integration-note">Financial arithmetic and completeness checks passed. Staff review and document authenticity are separate.</p>}<p className="integration-note">Summary field source evidence: {JSON.stringify(data.fieldEvidence||{})}</p><pre className="overflow-auto max-h-80 text-xs">{JSON.stringify({checks:data.checks,score:data.score||data.readerScore,modelVersion:data.modelVersion},null,2)}</pre></details>
    <div className="integration-toolbar mt-5"><Search size={17}/><label className="grow">Search transactions<input value={query} placeholder="Date, description, reference" onChange={e=>setQuery(e.target.value)}/></label></div>
    <div className="integration-table-wrap" tabIndex={0} role="region" aria-label="ABA transactions"><table className="integration-table"><thead><tr><th>Date</th><th>Description</th><th>Money in</th><th>Money out</th><th>Posted balance</th><th>Flags</th><th>Source</th></tr></thead><tbody>{filtered.slice(page*20,(page+1)*20).map(t=>{const flags=thresholdFlagsForRow(t);const lowBalance=flags.some(flag=>flag.type==='TRANSACTION_BALANCE_BELOW_MINIMUM');return <tr key={t.id}><td>{t.date}</td><td className="integration-description">{t.description}</td><td>{t.money_in?money(t.money_in,t.currency||currency):'—'}</td><td>{t.money_out?money(t.money_out,t.currency||currency):'—'}</td><td className={lowBalance?'integration-low-balance-amount':''}>{t.balance==null?'Balance unavailable':money(t.balance,t.balanceCurrency||t.currency||currency)}</td><td>{flags.length?flags.map(flag=><span key={flag.type} className={flag.type==='TRANSACTION_BALANCE_BELOW_MINIMUM'?'integration-review-balance-flag':'integration-review-flag-label'}>{flag.label}</span>):'—'}</td><td>Row {t.id} · page {t.page}</td></tr>;})}</tbody></table>{!filtered.length&&<p className="integration-note">No matching transactions.</p>}</div>
    <div className="integration-pagination"><span>{filtered.length} rows · page {page+1} of {Math.max(1,Math.ceil(filtered.length/20))}</span><div><button type="button" disabled={page===0} onClick={()=>setPage(p=>p-1)} aria-label="Previous transaction page"><ChevronLeft size={16}/></button><button type="button" disabled={(page+1)*20>=filtered.length} onClick={()=>setPage(p=>p+1)} aria-label="Next transaction page"><ChevronRight size={16}/></button></div></div>
    </details>
    {data.audit?.length>0&&<details className="mt-5"><summary>Bank correction and review audit</summary><pre className="overflow-auto max-h-72 text-xs">{JSON.stringify(data.audit,null,2)}</pre></details>}
  </section>;
}
