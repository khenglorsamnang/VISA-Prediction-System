import { useRef, useState } from "react";
import { api } from "../pages/pageUtils";

const SOURCE_CATEGORIES = [
  ["SALARY", "Salary / ប្រាក់ខែ"],
  ["BUSINESS_INCOME", "Business Income / ចំណូលអាជីវកម្ម"],
  ["FAMILY_SUPPORT", "Family Support / គ្រួសារជួយ"],
  ["PERSONAL_SAVINGS", "Personal Savings / ប្រាក់សន្សំ"],
  ["OTHER", "Other / ផ្សេងៗ"],
  ["UNKNOWN", "Unknown / មិនដឹងប្រភពច្បាស់លាស់"],
];

export function Metric({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
    </div>
  );
}

export function AutomatedVerificationReport({ verification, isPhoto = false }) {
  const checks = verification.checks || [];
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-semibold text-slate-900">
            {isPhoto ? "Applicant photo validation" : "Automated verification"}:{" "}
            {verification.status || "NOT AVAILABLE"}
          </h4>
          <p className="mt-1 text-sm text-slate-600">
            Document score: {verification.documentScore ?? "—"}/100 ·{" "}
            {isPhoto
              ? "OCR confidence is not used for photo checks"
              : `OCR confidence: ${
                  verification.ocrConfidencePercent == null
                    ? "Unavailable"
                    : `${verification.ocrConfidencePercent}%`
                }`}
          </p>
        </div>
        <p className="text-sm font-semibold text-slate-700">
          Risk: {verification.riskLevel || "UNAVAILABLE"}
          {verification.riskScore == null
            ? ""
            : ` (${verification.riskScore}/100)`}
        </p>
      </div>
      <p className="text-xs text-slate-600">
        Checked{" "}
        {verification.verificationTimestamp
          ? new Date(verification.verificationTimestamp).toLocaleString()
          : "time unavailable"}
        . Authenticity was not established.
      </p>
      {checks.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {[
            [
              "Passed checks",
              checks.filter((check) => check.status === "PASSED"),
            ],
            [
              "Unresolved checks",
              checks.filter((check) => check.status !== "PASSED"),
            ],
          ].map(([title, items]) => (
            <div key={title} className="rounded-lg bg-white p-3">
              <h5 className="text-sm font-semibold text-slate-800">{title}</h5>
              {items.length ? (
                <ul className="mt-2 space-y-2 text-xs text-slate-700">
                  {items.map((check) => (
                    <li key={check.code}>
                      <span
                        className={`font-semibold ${
                          check.status === "PASSED"
                            ? "text-emerald-700"
                            : check.status === "FAILED"
                              ? "text-red-700"
                              : "text-amber-800"
                        }`}
                      >
                        {check.label} · {check.status}
                      </span>
                      <p>{check.reason}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-slate-500">None</p>
              )}
            </div>
          ))}
        </div>
      )}
      {verification.warnings?.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-amber-800">
          {verification.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function BankStatementAnalysis({
  analysis,
  requirementScore,
  documentId,
  canManage = false,
  onChange,
}) {
  const [showDetails, setShowDetails] = useState(false);
  const [sourceReviewInputs, setSourceReviewInputs] = useState({});
  const [expandedSourceReviews, setExpandedSourceReviews] = useState({});
  const [sourceReviewResults, setSourceReviewResults] = useState({});
  const [sourceReviewAnalysis, setSourceReviewAnalysis] = useState(null);
  const [sourceReviewError, setSourceReviewError] = useState("");
  const [sourceReviewNotice, setSourceReviewNotice] = useState("");
  const [sourceReviewBusy, setSourceReviewBusy] = useState(false);
  const sourceReviewSaving = useRef(false);
  const [query, setQuery] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [direction, setDirection] = useState("all");
  const [page, setPage] = useState(0);
  const transactions = analysis.transactions || [];
  const invalid = analysis.invalidTransactions || [];
  const monthly = analysis.transactionSummary?.monthly || [];
  const checks = analysis.monthlyBalanceChecks || [];
  const currentBankScore =
    analysis.bankStatementScore ||
    requirementScore?.bankDiagnosticScore ||
    requirementScore?.score ||
    {};
  const sourceReviewAnalysisIsAhead =
    sourceReviewAnalysis &&
    (sourceReviewAnalysis.bankStatementScore?.points !== currentBankScore.points ||
      sourceReviewAnalysis.sourceReviewStatus !==
        (analysis.sourceReviewStatus || requirementScore?.sourceReviewStatus) ||
      (sourceReviewAnalysis.bankStatementScore?.transactionReviews || []).some(
        (review) =>
          (currentBankScore.transactionReviews || []).find(
            (currentReview) =>
              currentReview.transactionIndex === review.transactionIndex,
          )?.status !== review.status,
      ));
  const activeSourceReviewAnalysis = sourceReviewAnalysisIsAhead
    ? sourceReviewAnalysis
    : null;
  const bankScore =
    activeSourceReviewAnalysis?.bankStatementScore ||
    requirementScore?.bankDiagnosticScore ||
    requirementScore?.score ||
    analysis.bankStatementScore ||
    {};
  const documentScore =
    requirementScore?.documentScore || analysis.documentScore || bankScore;
  const computedBankScore =
    activeSourceReviewAnalysis?.computedBankScore ||
    requirementScore?.computedBankScore ||
    analysis.computedBankScore;
  const confirmedBankScore = activeSourceReviewAnalysis?.confirmedBankScore || requirementScore?.confirmedBankScore || analysis.confirmedBankScore || {
    status: "NOT_YET_CONFIRMED",
    maximum: bankScore.maximum,
  };
  const requirementContribution =
    activeSourceReviewAnalysis?.requirementContribution ||
    requirementScore?.requirementContribution ||
    analysis.requirementContribution;
  const requirementPointsEarned =
    activeSourceReviewAnalysis?.bankRequirementPointsEarned ??
    requirementScore?.bankRequirementPointsEarned ??
    analysis.bankRequirementPointsEarned;
  const requirementPointsMaximum =
    activeSourceReviewAnalysis?.bankRequirementPointsMaximum ??
    requirementScore?.bankRequirementPointsMaximum ??
    requirementScore?.maximumConfiguredPoints ??
    analysis.bankRequirementPointsMaximum ??
    requirementScore?.configuredPoints;
  const bankScorePercentage =
    computedBankScore?.available &&
    computedBankScore.points != null &&
    Number(computedBankScore.maximum) > 0
      ? (
          (Number(computedBankScore.points) /
            Number(computedBankScore.maximum)) *
          100
        ).toFixed(2)
      : null;
  const reviewRequirements = bankScore.transactionReviewRequirements || {};
  const deductionPoints = reviewRequirements.unverifiedSourcePenaltyPoints ?? 0.5;
  const bankScoreMaximum =
    computedBankScore?.maximum ??
    bankScore.maximum ??
    reviewRequirements.maximumBankScore;
  const deductionText =
    typeof deductionPoints === "number" &&
    Number.isFinite(deductionPoints) &&
    typeof bankScoreMaximum === "number" &&
    Number.isFinite(bankScoreMaximum) &&
    bankScoreMaximum > 0
      ? `−${deductionPoints.toFixed(2)} points (−${((deductionPoints / bankScoreMaximum) * 100).toFixed(2)}%)`
      : null;
  const reviewableTransactions = (bankScore.transactionReviews || []).filter(
    (transaction) => transaction.requiresStaffReview === true,
  );
  const reviewedBalanceIndices = new Set(
    reviewableTransactions
      .filter((transaction) =>
        transaction.transactionFlags?.some(
          (flag) => flag.flagType === "TRANSACTION_BALANCE_BELOW_MINIMUM",
        ),
      )
      .map((transaction) => transaction.transactionIndex),
  );
  const additionalBalanceFlags = (bankScore.triggeredFlags || []).filter(
    (flag) =>
      flag.flagType === "TRANSACTION_BALANCE_BELOW_MINIMUM" &&
      !reviewedBalanceIndices.has(flag.transactionIndex),
  );
  const pageSize = 10;
  const money = (value, currency = "USD") => {
    if (value == null) return "—";
    const code = String(currency || "").toUpperCase();
    if (["USD", "KHR", "EUR", "THB", "GBP", "CAD", "AUD"].includes(code)) {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: code,
        maximumFractionDigits: 2,
      }).format(Number(value));
    }
    return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency || ""}`.trim();
  };
  const monthlyBalance = (value, currency = "USD") => {
    const amount = Number(value);
    if (value == null || !Number.isFinite(amount)) return "—";
    return `${String(currency || "USD").toUpperCase()} ${amount.toLocaleString(
      undefined,
      { minimumFractionDigits: 2, maximumFractionDigits: 2 },
    )}`;
  };
  const filtered = transactions.filter((item) => {
    const text = [
      item.date,
      item.description,
      item.amount,
      item.direction,
      item.currency,
      item.page,
      item.evidence,
    ]
      .filter((value) => value != null)
      .join(" ")
      .toLowerCase();
    return (
      text.includes(query.trim().toLowerCase()) &&
      (!fromDate || (item.date && item.date >= fromDate)) &&
      (!toDate || (item.date && item.date <= toDate)) &&
      (direction === "all" || item.direction === direction)
    );
  });
  const pages = Math.ceil(filtered.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, pages - 1));
  const visible = filtered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
  async function reviewTransaction(transaction, action) {
    if (sourceReviewSaving.current) return;
    const input = sourceReviewInputs[transaction.transactionIndex] || {};
    const notes = (input.notes ?? transaction.reviewNotes ?? "").trim();
    const sourceCategory = input.sourceCategory ?? transaction.sourceCategory ?? "";
    if (!sourceCategory) {
      setSourceReviewError("Choose a source category before saving either decision.");
      return;
    }
    if (action === "CONFIRM_SOURCE" && sourceCategory === "UNKNOWN") {
      setSourceReviewError("Unknown source can only be marked unverified.");
      return;
    }
    if (sourceCategory === "OTHER" && !notes) {
      setSourceReviewError("A Staff Review Note is required when “Other / ផ្សេងៗ” is selected.");
      return;
    }
    if (action === "SOURCE_UNVERIFIED" && !deductionText) {
      setSourceReviewError("The source-review deduction is unavailable.");
      return;
    }
    sourceReviewSaving.current = true;
    setSourceReviewBusy(true);
    setSourceReviewError("");
    setSourceReviewNotice("");
    try {
      const { data: response } = await api.post(
        `/bank-statements/documents/${documentId}/transactions/${transaction.transactionIndex}/source-review`,
        {
          action,
          sourceCategory,
          notes,
          evidenceReferences: transaction.evidenceReferences || [],
        },
      );
      if (response?.bankStatementAnalysis) {
        setSourceReviewAnalysis(response.bankStatementAnalysis);
      }
      const index = transaction.transactionIndex;
      const audit = response?.audit || {};
      const status = audit.status || (action === "CONFIRM_SOURCE" ? "CONFIRMED" : "UNVERIFIED");
      const auditHistory = (Array.isArray(response?.auditHistory) ? response.auditHistory : [])
        .filter((entry) => entry.transactionIndex === index);
      if (auditHistory.length === 0 && audit.status) auditHistory.push(audit);
      const savedSourceCategory = audit.sourceCategory || sourceCategory;
      const sourceCategoryLabel =
        audit.sourceCategoryLabel ||
        SOURCE_CATEGORIES.find(([value]) => value === savedSourceCategory)?.[1];
      setSourceReviewResults((current) => ({
        ...current,
        [index]: {
          status,
          sourceCategory: savedSourceCategory,
          sourceCategoryLabel,
          reviewerId: audit.reviewerId,
          reviewedAt: audit.reviewedAt,
          notes: audit.notes ?? notes,
          reviewHistory: auditHistory,
        },
      }));
      setSourceReviewInputs((current) => ({
        ...current,
        [index]: {
          ...current[index],
          sourceCategory: savedSourceCategory,
          notes: audit.notes ?? notes,
        },
      }));
      setExpandedSourceReviews((current) => ({ ...current, [index]: false }));
      setSourceReviewNotice(`Transaction source review saved: ${status}.`);
      try {
        await onChange?.();
      } catch (refreshError) {
        setSourceReviewError(
          `The decision was saved, but the latest review could not be refreshed: ${refreshError.response?.data?.message || refreshError.message}`,
        );
      }
    } catch (error) {
      setSourceReviewError(
        error.response?.data?.message || error.message || "Review could not be saved.",
      );
    } finally {
      sourceReviewSaving.current = false;
      setSourceReviewBusy(false);
    }
  }
  function updateSourceReviewInput(index, key, value) {
    setSourceReviewInputs((current) => ({
      ...current,
      [index]: { ...current[index], [key]: value },
    }));
  }

  return (
    <section className="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold text-slate-900">
            Bank statement financial checks
          </h4>
          <p className="mt-1 text-xs text-slate-600">
            {analysis.financialRequirementStatus || "INSUFFICIENT_DATA"} ·
            authenticity is not confirmed
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-blue-900">
            Current calculated score:{" "}
            {computedBankScore?.available
              ? `${Number(computedBankScore.points).toFixed(2)} / ${computedBankScore.maximum}${bankScorePercentage == null ? "" : ` (${bankScorePercentage}%)`}`
              : `Unavailable / ${computedBankScore?.maximum ?? bankScore.maximum ?? "—"}`}
          </span>
          <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-blue-900">
            Requirement Contribution:{" "}
            {requirementContribution?.available && requirementContribution.points != null
              ? `${Number(requirementContribution.points).toFixed(2)} / ${requirementContribution.maximum}`
              : `Unavailable / ${requirementContribution?.maximum ?? "—"}`}
          </span>
          <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-blue-900">
            Confirmed Application Points:{" "}
            {requirementPointsMaximum == null
              ? "Unavailable"
              : `${requirementPointsEarned == null ? "Not Yet Earned" : requirementPointsEarned} / ${requirementPointsMaximum}`}
          </span>
          <span className="rounded-lg bg-white px-3 py-2 text-sm font-bold text-emerald-800">
            Confirmed Bank Score:{" "}
            {confirmedBankScore.status === "CONFIRMED"
              ? `${confirmedBankScore.points} / ${confirmedBankScore.maximum}`
              : "Not Yet Confirmed"}
          </span>
          <button
            type="button"
            aria-expanded={showDetails}
            onClick={() => setShowDetails((value) => !value)}
            className="rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm font-semibold text-blue-800 hover:bg-blue-50"
          >
            {showDetails ? "Hide details" : "View detailed analysis"}
          </button>
        </div>
      </div>
      {showDetails && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 rounded-lg bg-white p-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Financial Evidence", analysis.financialEvidenceStatus],
              ["Balance Reconciliation", analysis.balanceReconciliationStatus || analysis.reconciliation?.status],
              ["Monthly Balance Verification", analysis.monthlyBalanceVerification],
              ["Financial Assessment", analysis.financialAssessmentStatus || analysis.financialRequirementStatus],
              ["Staff Review", requirementScore?.staffReviewStatus || analysis.staffReviewStatus],
              ["Transaction Source Review", activeSourceReviewAnalysis?.sourceReviewStatus || requirementScore?.sourceReviewStatus || analysis.sourceReviewStatus],
              ["Document Review", requirementScore?.documentReviewStatus || analysis.documentReviewStatus || analysis.reviewStatus],
              ["Authenticity", analysis.authenticityStatus],
            ].map(([label, value]) => (
              <div key={label}>
                <p className="font-semibold text-slate-700">{label}</p>
                <p className="mt-1 text-slate-600">{value || "NOT_AVAILABLE"}</p>
              </div>
            ))}
          </div>
          {computedBankScore?.components?.length > 0 && (
            <details className="rounded-lg bg-white p-3 text-xs">
              <summary className="cursor-pointer font-semibold text-slate-800">
                Evidence details
              </summary>
              <ul className="mt-2 space-y-3">
                {computedBankScore.components.map((component) => (
                  <li key={component.name}>
                    <strong>{component.name}:</strong>{" "}
                    {component.explanation || "Evidence unavailable."}
                    {component.evidence &&
                      Object.keys(component.evidence).length > 0 && (
                        <pre className="mt-1 overflow-auto rounded bg-slate-50 p-2 text-[11px]">
                          {JSON.stringify(component.evidence, null, 2)}
                        </pre>
                      )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(activeSourceReviewAnalysis?.bankStatementScore || requirementScore?.bankDiagnosticScore || analysis.bankStatementScore) &&
            (documentScore?.available ?? true) && (
            <div className="rounded-lg bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h5 className="font-semibold text-slate-800">Bank assessment</h5>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700">
                  {bankScore.assessment || "INSUFFICIENT_DATA"}
                </span>
                {bankScore.manualReviewRequired && (
                  <span className="rounded-md bg-amber-100 px-2 py-1 text-xs font-bold text-amber-900">
                    Manual review
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-slate-600">
                {bankScorePercentage == null
                  ? "Bank score percentage unavailable"
                  : `Bank score percentage: ${bankScorePercentage}% (${computedBankScore.points} / ${computedBankScore.maximum} × 100)`} ·
                {` ${bankScore.assessmentNote || "Human review is required."}`}
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {[
                  ["ending_balance", "Ending Balance"],
                  ["starting_balance", "Starting Balance"],
                  ["transaction_history", "Transaction History"],
                  ["transaction_period", "Transaction Period"],
                ].map(([key, label]) => {
                  const item = bankScore.components?.[key];
                  return (
                    <p key={key} className="rounded bg-slate-50 p-2 text-xs">
                      <strong>{label}:</strong>{" "}
                      {item?.points == null
                        ? `INSUFFICIENT_DATA / ${item?.maximum ?? "—"}`
                        : `${Number(item.points).toFixed(2)} / ${item.maximum ?? "—"}`}
                    </p>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-slate-600">
                OCR confidence:{" "}
                {analysis.ocrConfidence == null
                  ? "Unavailable"
                  : `${Math.round(Number(analysis.ocrConfidence) * 100)}%`}
                {" · "}Balance reconciliation:{" "}
                {analysis.balanceReconciliationStatus ||
                  analysis.reconciliation?.status ||
                  "NOT_AVAILABLE"}
              </p>
              <p className="mt-3 text-xs">
                Total Computed Bank Score: {computedBankScore?.points == null ? "INSUFFICIENT_DATA" : `${Number(computedBankScore.points).toFixed(2)} / ${computedBankScore.maximum ?? "—"}`} ·
                Confirmed Bank Score: {confirmedBankScore?.status === "CONFIRMED" ? `${Number(confirmedBankScore.points).toFixed(2)} / ${confirmedBankScore.maximum}` : "Not Yet Confirmed"} ·
                Pending reviews: {bankScore.pendingTransactionReviews ?? "—"} ·
              </p>
              {bankScore.sourceReviewDeductionAudit && (
                <p className="mt-2 text-xs font-medium text-slate-700">
                  UNVERIFIED transactions: {bankScore.sourceReviewDeductionAudit.unverifiedTransactionCount ?? 0}
                  {" · "}Total deduction: −{Number(bankScore.sourceReviewDeductionAudit.totalDeductionPoints || 0).toFixed(2)} points
                  {" · "}Transaction History: {Number(bankScore.sourceReviewDeductionAudit.transactionHistoryCurrentPoints ?? 0).toFixed(2)}
                  / {Number(bankScore.sourceReviewDeductionAudit.transactionHistoryBasePoints ?? 9).toFixed(2)}
                </p>
              )}
            </div>
          )}
          {reviewableTransactions.length > 0 && (
            <section className="space-y-3 rounded-lg bg-white p-3">
              <h5 className="text-sm font-semibold text-slate-800">
                Staff Review
              </h5>
              <p className="text-xs text-slate-600">
                Source review is separate from the financial assessment and authenticity verification. An unverified source is not a fraud finding.
              </p>
              {sourceReviewError && <p role="alert" className="text-sm text-red-700">{sourceReviewError}</p>}
              {sourceReviewNotice && <p role="status" className="text-sm text-green-700">{sourceReviewNotice}</p>}
              {reviewableTransactions.map((transaction) => {
                const index = transaction.transactionIndex;
                const input = sourceReviewInputs[index] || {};
                const savedReview = sourceReviewResults[index] || {};
                const reviewHistory = savedReview.reviewHistory?.length
                  ? savedReview.reviewHistory
                  : transaction.reviewHistory || [];
                const reviewNotes = savedReview.notes ?? transaction.reviewNotes;
                const sourceCategory = input.sourceCategory ?? transaction.sourceCategory ?? "";
                const reviewNote = (input.notes ?? transaction.reviewNotes ?? "").trim();
                const noteRequired = sourceCategory === "OTHER";
                const sourceReviewFlags = transaction.transactionFlags || [];
                const hasLowBalanceFlag = sourceReviewFlags.some(
                  (flag) => flag.flagType === "TRANSACTION_BALANCE_BELOW_MINIMUM",
                );
                return (
                  <article key={index} className="rounded border border-slate-200 p-3 text-xs">
                    <p className="font-semibold">{transaction.date || "Date unavailable"} · {money(transaction.amount, transaction.currency)} · {transaction.direction || "Direction unavailable"}</p>
                    <p className="mt-1">{transaction.description || "Description unavailable"} · Page {transaction.page || "—"} · Ref {transaction.reference || "—"}</p>
                    <p className={`mt-1 ${hasLowBalanceFlag ? "integration-low-balance-amount" : ""}`}>
                      Balance: {transaction.balance == null || String(transaction.balance).trim() === ""
                        ? "Balance unavailable"
                        : money(transaction.balance, transaction.balanceCurrency || transaction.currency)}
                    </p>
                    <p className="mt-1">Sender: {transaction.sender || "Unavailable"} · Receiver: {transaction.receiver || transaction.counterparty || "Unavailable"}</p>
                    <p className="mt-1">USD equivalent: {money(transaction.amountUsd, "USD")} · Rate: {Number(transaction.exchangeRateToUsd).toFixed(6)} USD per {transaction.currency} · Review threshold: {money(transaction.staffReviewThresholdUsd, "USD")}</p>
                    <p className="mt-1 break-words">Source evidence: {transaction.sourceEvidence || "Unavailable"}</p>
                    <p className="mt-1">Risk indicators: {transaction.riskIndicators.length ? transaction.riskIndicators.map((item) => `${item.code}: ${item.reason}`).join(" · ") : "None identified; review is based on the configured amount threshold."}</p>
                    <p className="mt-1 font-semibold">Current status: {savedReview.status || transaction.status || "PENDING_REVIEW"}</p>
                    {sourceReviewFlags.length > 0 && (
                      <div className="mt-1 grid gap-1">
                        <strong>Informational flags</strong>
                        {sourceReviewFlags.map((flag) => (
                          <span
                            key={flag.flagType}
                            className={flag.flagType === "TRANSACTION_BALANCE_BELOW_MINIMUM"
                              ? "integration-review-balance-flag"
                              : "integration-review-flag-label"}
                          >
                            {flag.flagType === "MONEY_IN_THRESHOLD"
                              ? `Money In flag · ${money(flag.amountUsd, "USD")} ≥ ${money(flag.thresholdUsd, "USD")}`
                              : flag.flagType === "MONEY_OUT_THRESHOLD"
                                ? `Money Out flag · ${money(flag.amountUsd, "USD")} ≥ ${money(flag.thresholdUsd, "USD")}`
                                : `Balance below minimum · ${money(flag.amountUsd, "USD")} < ${money(flag.thresholdUsd, "USD")}`}
                            {" · no score deduction"}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="mt-1">Source review deduction: {Number(transaction.sourceDeduction?.points) > 0
                      ? `−${Number(transaction.sourceDeduction.points).toFixed(2)} points`
                      : transaction.sourceDeduction?.status === "PENDING_POLICY_EXCLUDED"
                        ? "No deduction · pending review"
                        : transaction.sourceDeduction?.status === "SOURCE_CONFIRMED"
                          ? "No deduction · source confirmed"
                          : transaction.sourceDeduction?.status === "PENDING_REVIEW"
                            ? "No deduction · pending review"
                            : "No source deduction"}</p>
                    {(savedReview.sourceCategoryLabel || transaction.sourceCategoryLabel) && <p className="mt-1">Source category: {savedReview.sourceCategoryLabel || transaction.sourceCategoryLabel}</p>}
                    {reviewNotes && <p className="mt-1 break-words">Staff Review Note: {reviewNotes}</p>}
                    {(savedReview.reviewerId || transaction.reviewerId) && <p className="mt-1">Reviewer {savedReview.reviewerId || transaction.reviewerId} · {savedReview.reviewedAt || transaction.reviewedAt}</p>}
                    {canManage && (
                      <button type="button" className="mt-3 rounded bg-blue-700 px-3 py-2 text-white" aria-expanded={Boolean(expandedSourceReviews[index])} onClick={() => setExpandedSourceReviews((current) => ({ ...current, [index]: !current[index] }))}>
                        {expandedSourceReviews[index] ? "Hide Staff Review" : "Staff Review"}
                      </button>
                    )}
                    {canManage && expandedSourceReviews[index] && (
                      <div className="mt-3 grid gap-2">
                        <label className="grid gap-1">ប្រភេទប្រភពប្រាក់
                          <select required aria-required="true" value={input.sourceCategory ?? transaction.sourceCategory ?? ""} onChange={(event) => updateSourceReviewInput(index, "sourceCategory", event.target.value)}>
                            <option value="">ជ្រើសរើសប្រភេទប្រភពប្រាក់</option>
                            {SOURCE_CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                          </select>
                        </label>
                        <label className="grid gap-1">Staff Review Note{noteRequired && <span className="text-red-700"> *</span>}
                          <textarea aria-required={noteRequired} rows={4} value={input.notes ?? transaction.reviewNotes ?? ""} onChange={(event) => updateSourceReviewInput(index, "notes", event.target.value)} placeholder="បញ្ចូលកំណត់ចំណាំ ឬភស្តុតាងដែលបានពិនិត្យ…" />
                        </label>
                        {noteRequired && !reviewNote && <p role="status" className="text-sm text-red-700">A Staff Review Note is required when “Other / ផ្សេងៗ” is selected.</p>}
                        <div className="flex flex-wrap gap-2">
                          <button type="button" aria-describedby={sourceCategory === "UNKNOWN" ? `unknown-source-help-${index}` : undefined} className="rounded bg-blue-700 px-3 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50" disabled={sourceReviewBusy || !sourceCategory || sourceCategory === "UNKNOWN" || (noteRequired && !reviewNote)} onClick={() => reviewTransaction(transaction, "CONFIRM_SOURCE")}>Confirm source</button>
                          <button type="button" className="rounded bg-amber-700 px-3 py-2 text-white disabled:opacity-50" disabled={sourceReviewBusy || !sourceCategory || (noteRequired && !reviewNote) || !deductionText} onClick={() => reviewTransaction(transaction, "SOURCE_UNVERIFIED")}>Source unverified</button>
                          <span aria-live="polite">
                            {deductionText
                              ? `Source unverified · ${deductionText}`
                              : "Source-review deduction unavailable"}
                          </span>
                        </div>
                        {sourceCategory === "UNKNOWN" && <p id={`unknown-source-help-${index}`} role="status" className="text-sm font-medium text-amber-800">ប្រភពប្រាក់មិនច្បាស់លាស់ — អាចជ្រើសរើស Source unverified បានតែប៉ុណ្ណោះ។</p>}
                      </div>
                    )}
                    {reviewHistory.length > 0 && (
                      <details className="mt-3">
                        <summary className="cursor-pointer font-semibold">Review history ({reviewHistory.length})</summary>
                        <ol className="mt-2 list-decimal space-y-2 pl-5">
                          {reviewHistory.map((entry, historyIndex) => (
                            <li key={`${entry.reviewedAt || "review"}-${historyIndex}`}>
                              <strong>{entry.status || "PENDING_REVIEW"}</strong>
                              {entry.sourceCategoryLabel && <> · {entry.sourceCategoryLabel}</>}
                              {" · "}{entry.reviewerId || "Reviewer unavailable"} · {entry.reviewedAt || "Date unavailable"}
                              <p className="mt-1 break-words">{entry.notes || "No note recorded"}</p>
                              {entry.evidenceReferences?.length > 0 && <p>Evidence references: {entry.evidenceReferences.join(", ")}</p>}
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </article>
                );
              })}
              {analysis.sourceReviewAudit?.length > 0 && (
                <details><summary className="cursor-pointer font-semibold">Transaction review audit history</summary><pre className="mt-2 max-h-64 overflow-auto text-xs">{JSON.stringify(analysis.sourceReviewAudit, null, 2)}</pre></details>
              )}
            </section>
          )}
          {additionalBalanceFlags.length > 0 && (
            <section className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs">
              <h5 className="font-semibold text-red-800">Low transaction balances · informational</h5>
              <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                {additionalBalanceFlags.map((flag) => (
                  <li key={`${flag.transactionId}-${flag.flagType}`} className="rounded-md border border-red-200 bg-white p-2">
                    <p className="font-semibold">
                      {flag.date || "Date unavailable"} ·{" "}
                      {flag.transactionAmountUsd == null
                        ? "Transaction amount unavailable"
                        : money(flag.transactionAmountUsd, "USD")}{" "}
                      · {flag.direction || "Direction unavailable"}
                    </p>
                    <p className="mt-1 integration-low-balance-amount">
                      Balance below minimum · {money(flag.balanceUsd ?? flag.amountUsd, "USD")}{" "}
                      &lt; {money(flag.thresholdUsd, "USD")}
                    </p>
                    <p className="mt-1 text-slate-600">
                      Ref {flag.transactionReference || "Unavailable"}
                      {flag.page ? ` · Page ${flag.page}` : ""}
                    </p>
                    <p className="mt-1 text-slate-600">Informational only · no score deduction</p>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Bank", analysis.accountInformation?.bankName],
              [
                "Account holder",
                analysis.accountInformation?.accountHolderName,
              ],
              ["Account number", analysis.accountInformation?.accountNumber],
              ["Currency", analysis.accountInformation?.currency],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg bg-white p-3">
                <p className="text-xs text-slate-500">{label}</p>
                <p className="mt-1 break-words text-sm font-semibold text-slate-800">
                  {value || "NOT_AVAILABLE"}
                </p>
              </div>
            ))}
          </div>
          <div className="rounded-lg bg-white p-3 text-sm">
            <h5 className="font-semibold text-slate-800">
              OCR and statement period
            </h5>
            <p className="mt-1 text-xs text-slate-600">
              {analysis.ocrProcessingSummary?.status || "NOT_AVAILABLE"} ·{" "}
              {analysis.ocrProcessingSummary?.pagesProcessed ?? 0}/
              {analysis.ocrProcessingSummary?.totalPages ??
                analysis.documentInformation?.ocrPageCount ??
                0}{" "}
              OCR pages · {transactions.length} normalized transactions ·{" "}
              {invalid.length} requiring review
            </p>
            <p className="mt-1 text-xs text-slate-600">
              {analysis.statementPeriod?.startDate || "Start unavailable"} to{" "}
              {analysis.statementPeriod?.endDate || "end unavailable"} ·{" "}
              {analysis.statementPeriod?.status || "INSUFFICIENT_DATA"}
            </p>
          </div>
          <div className="overflow-x-auto rounded-lg bg-white p-3">
            <h5 className="text-sm font-semibold text-slate-800">
              Monthly financial summary
            </h5>
            <table className="mt-2 w-full min-w-[750px] text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1 pr-3">Month</th>
                  <th className="py-1 pr-3">Credits</th>
                  <th className="py-1 pr-3">Debits</th>
                  <th className="py-1 pr-3">Transactions</th>
                  <th className="py-1 pr-3">Closing balance</th>
                  <th className="py-1 pr-3">Evidence status</th>
                  <th className="py-1">Source</th>
                </tr>
              </thead>
              <tbody>
                {monthly.length ? (
                  monthly.map((item) => {
                    const check = checks.find((entry) => entry.month === item.month);
                    const closingBalance =
                      check?.closingBalance ?? item.closingBalance;
                    const minimumBalanceStatus =
                      check?.minimumBalanceStatus ?? item.minimumBalanceStatus;
                    const minimumRequired =
                      check?.minimumRequired ?? item.minimumRequired;
                    const shortfall = check?.shortfall ?? item.shortfall;
                    return (
                    <tr key={item.month} className="border-t border-slate-100">
                      <td className="py-1 pr-3">{item.month}</td>
                      <td className="py-1 pr-3">
                        {money(item.totalDeposits, item.currency)}
                      </td>
                      <td className="py-1 pr-3">
                        {money(item.totalWithdrawals, item.currency)}
                      </td>
                      <td className="py-1 pr-3">
                        {item.transactionCount ??
                          (item.incomingCount || 0) + (item.outgoingCount || 0)}
                      </td>
                      <td className="py-1 pr-3">
                        {minimumBalanceStatus === "BELOW_MINIMUM" ? (
                          <>
                            <p className="font-semibold text-red-700">Below minimum · manual review</p>
                            <p className="mt-1 font-medium text-red-700">
                              Closing balance: {monthlyBalance(closingBalance, item.currency)} ·
                              Minimum: {monthlyBalance(minimumRequired, "USD")} ·
                              Shortfall: {monthlyBalance(shortfall, "USD")}
                            </p>
                          </>
                        ) : closingBalance == null ? (
                          "Balance unavailable"
                        ) : (
                          monthlyBalance(closingBalance, item.currency)
                        )}
                      </td>
                      <td className="py-1 pr-3">
                        {check?.status || item.closingBalanceStatus || "NOT_AVAILABLE"}
                      </td>
                      <td className="py-1">
                        {check?.sourcePage
                          ? `Page ${check.sourcePage}${check.sourceTransaction ? ` · ref ${check.sourceTransaction}` : ""}${check.sourceDate ? ` · ${check.sourceDate}` : ""}`
                          : item.closingBalanceSource || "—"}
                      </td>
                    </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="7" className="py-2 text-slate-500">
                      No monthly evidence is available.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Balance reconciliation</h5>
              <p className="mt-1">
                {analysis.reconciliation?.status || "NOT_AVAILABLE"}
              </p>
              <p>
                Expected:{" "}
                {money(analysis.reconciliation?.expected_closing_balance)} ·
                Extracted:{" "}
                {money(analysis.reconciliation?.extracted_closing_balance)}
              </p>
            </div>
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Account holder evidence</h5>
              <p className="mt-1">
                The extracted name is retained for audit. It is not compared
                with the applicant name or treated as proof of legal ownership.
              </p>
              <p className="mt-1">
                {analysis.accountHolderCheck?.status || "INSUFFICIENT_DATA"} ·{" "}
                {analysis.accountHolderCheck?.reason ||
                  "Name evidence unavailable."}
              </p>
            </div>
          </div>
          {analysis.sourceOfFundsAnalysis && (
            <div className="rounded-lg bg-white p-3 text-xs">
              <h5 className="font-semibold">Source of funds</h5>
              <p className="mt-1">
                {analysis.sourceOfFundsAnalysis.status || "INSUFFICIENT_DATA"}
              </p>
              <p>{analysis.sourceOfFundsAnalysis.note}</p>
            </div>
          )}
          {analysis.reviewFlags?.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">
              <h5 className="font-semibold text-amber-900">Review flags</h5>
              <ul className="mt-1 list-disc pl-5">
                {analysis.reviewFlags.map((flag, index) => (
                  <li key={`${flag.code}-${index}`}>{flag.reason}</li>
                ))}
              </ul>
            </div>
          )}
          {(transactions.length > 0 || invalid.length > 0) && (
            <details className="rounded-lg bg-white p-3">
              <summary className="cursor-pointer text-sm font-semibold">
                Transaction history ({filtered.length} filtered ·{" "}
                {transactions.length} total · {invalid.length} invalid)
              </summary>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <input
                  type="search"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setPage(0);
                  }}
                  placeholder="Search transactions"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <input
                  type="date"
                  value={fromDate}
                  onChange={(event) => {
                    setFromDate(event.target.value);
                    setPage(0);
                  }}
                  aria-label="From date"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <input
                  type="date"
                  value={toDate}
                  onChange={(event) => {
                    setToDate(event.target.value);
                    setPage(0);
                  }}
                  aria-label="To date"
                  className="rounded-lg border px-3 py-2 text-sm"
                />
                <select
                  value={direction}
                  onChange={(event) => {
                    setDirection(event.target.value);
                    setPage(0);
                  }}
                  className="rounded-lg border px-3 py-2 text-sm"
                >
                  <option value="all">All directions</option>
                  <option value="debit">Debit</option>
                  <option value="credit">Credit</option>
                </select>
              </div>
              <div className="mt-3 max-h-72 overflow-auto">
                <table className="w-full min-w-[850px] text-left text-xs">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1 pr-2">Date</th>
                      <th className="py-1 pr-2">Description</th>
                      <th className="py-1 pr-2">Debit</th>
                      <th className="py-1 pr-2">Credit</th>
                      <th className="py-1 pr-2">Balance</th>
                      <th className="py-1 pr-2">Currency</th>
                      <th className="py-1 pr-2">Page</th>
                      <th className="py-1 pr-2">Status</th>
                      <th className="py-1">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((item, index) => (
                      <tr
                        key={`${item.date}-${item.page}-${currentPage}-${index}`}
                        className="border-t"
                      >
                        <td className="py-1 pr-2">{item.date || "—"}</td>
                        <td className="py-1 pr-2">{item.description || "—"}</td>
                        <td className="py-1 pr-2">
                          {item.direction === "debit"
                            ? money(item.amount, item.currency)
                            : "—"}
                        </td>
                        <td className="py-1 pr-2">
                          {item.direction === "credit"
                            ? money(item.amount, item.currency)
                            : "—"}
                        </td>
                        <td className="py-1 pr-2">
                          {money(item.running_balance, item.currency)}
                        </td>
                        <td className="py-1 pr-2">{item.currency || "—"}</td>
                        <td className="py-1 pr-2">{item.page ?? "—"}</td>
                        <td className="py-1 pr-2">
                          {item.extraction_status || "NEEDS_REVIEW"}
                        </td>
                        <td className="py-1">{item.evidence || "—"}</td>
                      </tr>
                    ))}
                    {!visible.length && (
                      <tr>
                        <td colSpan="9" className="py-3 text-slate-500">
                          No matching transactions.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex items-center justify-between text-xs">
                <span>
                  {filtered.length
                    ? `Transaction page ${currentPage + 1} of ${pages}`
                    : "No results"}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!currentPage}
                    onClick={() => setPage(currentPage - 1)}
                    className="rounded border px-3 py-1 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={!pages || currentPage >= pages - 1}
                    onClick={() => setPage(currentPage + 1)}
                    className="rounded border px-3 py-1 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            </details>
          )}
        </div>
      )}
      <p className="text-xs text-slate-500">{analysis.disclaimer}</p>
    </section>
  );
}
