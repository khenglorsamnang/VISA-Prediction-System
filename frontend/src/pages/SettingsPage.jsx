import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  Download,
  LoaderCircle,
  RefreshCw,
  Save,
  Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "./pageUtils";

const initialLabelMappings = {
  visa: { APPROVED: "APPROVED", REJECTED: "REJECTED" },
  financial: { LOW: "LOW", MEDIUM: "MEDIUM", HIGH: "HIGH" },
};

function messageFrom(error) {
  return (
    error?.response?.data?.message ||
    error?.response?.data?.detail ||
    error?.message ||
    "The request could not be completed."
  );
}

export default function SettingsPage({ profile }) {
  const { t } = useTranslation();
  const [rulesText, setRulesText] = useState("");
  const [modelStatus, setModelStatus] = useState(null);
  const [modelHistory, setModelHistory] = useState([]);
  const [modelType, setModelType] = useState("visa");
  const [algorithm, setAlgorithm] = useState("lightgbm");
  const [labelMapping, setLabelMapping] = useState(
    JSON.stringify(initialLabelMappings.visa, null, 2),
  );
  const [dataset, setDataset] = useState(null);
  const [datasetValidation, setDatasetValidation] = useState(null);
  const [validatingDataset, setValidatingDataset] = useState(false);
  const [downloadingTemplate, setDownloadingTemplate] = useState(false);
  const [sourceDescription, setSourceDescription] = useState("");
  const [verifiedOutcomes, setVerifiedOutcomes] = useState(false);
  const [privacyReviewed, setPrivacyReviewed] = useState(false);
  const [validationReport, setValidationReport] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [releaseCriteria, setReleaseCriteria] = useState("");
  const [approvalComment, setApprovalComment] = useState("");
  const [rejectionComment, setRejectionComment] = useState("");
  const [activationComment, setActivationComment] = useState("");
  const [rollbackComment, setRollbackComment] = useState("");
  const [confirmedActions, setConfirmedActions] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const isAdmin = profile?.role === "ADMIN";

  const refresh = useCallback(
    () =>
      Promise.all([
        api.get("/rules"),
        api.get("/ml/model-status"),
        api.get("/ml/metrics"),
      ])
        .then(([rulesResponse, statusResponse, metricsResponse]) => {
          setRulesText(JSON.stringify(rulesResponse.data.rules, null, 2));
          setModelStatus(statusResponse.data);
          setModelHistory(metricsResponse.data.models || []);
          setError("");
        })
        .catch((requestError) => {
          setError(messageFrom(requestError));
        })
        .finally(() => {
          setLoading(false);
        }),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveRules = async () => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const rules = JSON.parse(rulesText);
      const response = await api.put("/rules", { rules });
      setRulesText(JSON.stringify(response.data.rules, null, 2));
      setNotice("Internal rules saved.");
    } catch (requestError) {
      setError(
        requestError instanceof SyntaxError
          ? "Rules must be valid JSON."
          : messageFrom(requestError),
      );
    } finally {
      setSaving(false);
    }
  };

  const trainModel = async (event) => {
    event.preventDefault();
    if (!datasetValidation?.ready_to_train) {
      setError("Validate a complete dataset before training.");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const formData = new FormData();
      formData.append("file", dataset);
      formData.append("model_type", modelType);
      formData.append("algorithm", algorithm);
      formData.append("verified_outcomes", String(verifiedOutcomes));
      formData.append("privacy_reviewed", String(privacyReviewed));
      formData.append("source_description", sourceDescription);
      formData.append("label_mapping", labelMapping);
      const response = await api.post("/ml/train", formData);
      setNotice(response.data.message || "Model trained and queued for Admin review.");
      await refresh();
    } catch (requestError) {
      if (requestError?.response?.data?.validation_report) {
        setDatasetValidation(requestError.response.data.validation_report);
      }
      setError(messageFrom(requestError));
    } finally {
      setSaving(false);
    }
  };

  const validateDataset = async () => {
    if (!dataset) {
      setError("Choose a CSV or Excel dataset first.");
      return;
    }
    setValidatingDataset(true);
    setError("");
    setNotice("");
    setDatasetValidation(null);
    try {
      const formData = new FormData();
      formData.append("file", dataset);
      formData.append("model_type", modelType);
      formData.append("label_mapping", labelMapping);
      const response = await api.post("/ml/validate-dataset", formData);
      setDatasetValidation(response.data);
      if (response.data.ready_to_train) {
        setNotice("Dataset validation passed. You can train a candidate model.");
      }
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setValidatingDataset(false);
    }
  };

  const downloadDatasetTemplate = async () => {
    setDownloadingTemplate(true);
    setError("");
    try {
      const response = await api.get("/ml/dataset-template", {
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "ml_training_dataset_template.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setDownloadingTemplate(false);
    }
  };

  const performModelAction = async (model, action, payload, successMessage) => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await api.post(
        `/ml/models/${encodeURIComponent(model.model_version)}/${action}`,
        payload,
      );
      setNotice(successMessage(response.data));
      if (action === "review") {
        setValidationReport("");
        setReviewComment("");
      } else if (action === "approve") {
        setApprovalComment("");
      } else if (action === "reject") {
        setRejectionComment("");
      } else if (action === "activate") {
        setActivationComment("");
      } else if (action === "rollback") {
        setRollbackComment("");
      }
      if (action === "activate" || action === "rollback") {
        setConfirmedActions((current) => ({
          ...current,
          [`${model.model_version}:${action}`]: false,
        }));
      }
      await refresh();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setSaving(false);
    }
  };

  const confirmAction = (model, action) =>
    setConfirmedActions((current) => ({
      ...current,
      [`${model.model_version}:${action}`]: !current[`${model.model_version}:${action}`],
    }));

  const changeModelType = (value) => {
    setModelType(value);
    setLabelMapping(JSON.stringify(initialLabelMappings[value], null, 2));
    setDatasetValidation(null);
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">{t("settings")}</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-600">
            Internal financial review configuration and verified-data model management.
            Rules shown here are internal business rules, not official embassy requirements.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            setError("");
            void refresh();
          }}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </header>

      {error && (
        <p role="alert" className="flex items-center gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </p>
      )}
      {notice && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <LoaderCircle className="h-4 w-4 animate-spin" /> Loading configuration...
        </div>
      ) : (
        <>
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Financial and country rules</h2>
            <p className="mt-1 text-sm text-slate-600">
              Edit weights, thresholds, evidence requirements, and universal checks. The API validates the policy before saving.
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Bank transfer review uses <code>bank_statement_scoring.staff_review_threshold_usd</code> (default 1000). For non-USD transactions, configure <code>staff_review_exchange_rates_to_usd</code> as USD per one currency unit; no exchange rate is inferred.
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Money In, Money Out, and low-balance flags are informational and never reduce the score. Only a completed “Source unverified” review deducts 0.50 points; confirmed and pending reviews do not. Legacy per-flag deduction settings are normalized to these rules.
            </p>
            <textarea
              aria-label="Internal rules JSON"
              value={rulesText}
              onChange={(event) => setRulesText(event.target.value)}
              spellCheck="false"
              rows={22}
              className="mt-4 w-full rounded-lg border border-slate-300 p-3 font-mono text-xs text-slate-800"
              disabled={!isAdmin}
            />
            {isAdmin ? (
              <button
                type="button"
                onClick={() => void saveRules()}
                disabled={saving}
                className="mt-3 inline-flex items-center gap-2 rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              >
                {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                Save rules
              </button>
            ) : (
              <p className="mt-3 text-sm text-slate-500">Administrator role is required to change rules.</p>
            )}
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Model status and evaluation</h2>
            <p className="mt-1 text-sm text-slate-600">
              Models move through Admin review, approval, and explicit activation. A trainer may review and approve their own model; this is labeled Admin Self-Review.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {Object.entries(modelStatus?.models || {}).map(([type, model]) => (
                <article key={type} className="rounded-lg border border-slate-200 p-4">
                  <h3 className="font-semibold capitalize text-slate-900">{type} model</h3>
                  <p className="mt-1 text-sm text-slate-600">Status: {model.status}</p>
                  <p className="text-sm text-slate-600">Active version: {model.active_model_version || "None"}</p>
                  {model.latest && (
                    <div className="break-all text-xs text-slate-500">
                      <p>
                        Latest: {model.latest.model_version} · {model.latest.row_count} rows
                      </p>
                      <p>
                        Class counts: {JSON.stringify(model.latest.class_counts || {})}
                      </p>
                    </div>
                  )}
                </article>
              ))}
            </div>
            {!modelStatus?.prediction_available && (
              <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
                Visa approval probability unavailable: no Admin-approved visa model is active.
              </p>
            )}
            <h3 className="mt-5 font-semibold text-slate-900">Model version history</h3>
            <label className="mb-5 block text-sm font-medium text-slate-700">Release criteria for new ABA feature models<textarea value={releaseCriteria} minLength={20} onChange={e=>setReleaseCriteria(e.target.value)} className="mt-2 block w-full rounded-lg border border-slate-300 p-3" placeholder="Document acceptable calibration, cohort performance, sample coverage and limitations. No universal threshold is assumed." /></label>
          {modelHistory.map((model) => {
              const status = String(model.model_status || "").toUpperCase();
              const activeVersion =
                modelStatus?.models?.[model.model_type]?.active_model_version;
              const isActive =
                status === "ACTIVE" || activeVersion === model.model_version;
              const canRollback =
                isAdmin &&
                !isActive &&
                ["ROLLED_BACK", "ACTIVE"].includes(status) &&
                Boolean(model.approved_at) &&
                model.validation_passed !== false;
              const activateConfirmed =
                confirmedActions[`${model.model_version}:activate`] === true;
              const rollbackConfirmed =
                confirmedActions[`${model.model_version}:rollback`] === true;
              return (
                <details key={model.model_version} className="mt-3 rounded-lg border border-slate-200 p-3">
                  <summary className="cursor-pointer text-sm font-medium text-slate-800">
                    {model.model_version} · {status}
                  </summary>
                  <div className="mt-3 space-y-3">
                    <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-slate-600">
                      {JSON.stringify(model.metrics, null, 2)}
                    </pre>
                    <div className="break-all text-xs text-slate-600">
                      <p>Trained by: {model.trained_by || "Unknown"}</p>
                      {model.reviewed_by && (
                        <p>
                          {model.self_review ? "Admin Self-Review" : "Admin Review"} by {model.reviewed_by}
                        </p>
                      )}
                      {model.approved_by && <p>Approved by: {model.approved_by}</p>}
                      {model.activated_by && <p>Activated by: {model.activated_by}</p>}
                    </div>
                    {model.validation_report && (
                      <div className="rounded bg-slate-50 p-3 text-sm text-slate-700">
                        <strong>Validation report</strong>
                        <p className="mt-1 whitespace-pre-wrap">{model.validation_report}</p>
                      </div>
                    )}
                    {isAdmin && status === "PENDING_REVIEW" && (
                      <div className="space-y-3">
                        <label className="block text-sm text-slate-700">
                          Validation report
                          <textarea
                            value={validationReport}
                            onChange={(event) => setValidationReport(event.target.value)}
                            rows={3}
                            maxLength={2000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                        <label className="block text-sm text-slate-700">
                          Review comments
                          <textarea
                            value={reviewComment}
                            onChange={(event) => setReviewComment(event.target.value)}
                            rows={2}
                            maxLength={1000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            void performModelAction(
                              model,
                              "review",
                              { validation_report: validationReport, review_comment: reviewComment, releaseCriteria },
                              () => `Review started for ${model.model_version}.`,
                            )
                          }
                          disabled={saving || validationReport.trim().length < 20 || reviewComment.trim().length < 12}
                          className="rounded-lg bg-blue-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                        >
                          Start Admin review
                        </button>
                      </div>
                    )}
                    {isAdmin && status === "UNDER_REVIEW" && (
                      <div className="space-y-3">
                        <p className="text-sm font-medium text-blue-800">
                          {model.self_review ? "Admin Self-Review" : "Admin Review"} is in progress.
                        </p>
                        <label className="block text-sm text-slate-700">
                          Approval comments
                          <textarea
                            value={approvalComment}
                            onChange={(event) => setApprovalComment(event.target.value)}
                            rows={2}
                            maxLength={1000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              void performModelAction(
                                model,
                                "approve",
                                { approval_comment: approvalComment, releaseCriteria },
                                () => `Approved ${model.model_version}.`,
                              )
                            }
                            disabled={saving || approvalComment.trim().length < 12}
                            className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                          >
                            Approve model
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              void performModelAction(
                                model,
                                "reject",
                                { rejection_comment: rejectionComment },
                                () => `Rejected ${model.model_version}.`,
                              )
                            }
                            disabled={saving || rejectionComment.trim().length < 12}
                            className="rounded-lg border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50"
                          >
                            Reject model
                          </button>
                        </div>
                        <label className="block text-sm text-slate-700">
                          Rejection comments
                          <textarea
                            value={rejectionComment}
                            onChange={(event) => setRejectionComment(event.target.value)}
                            rows={2}
                            maxLength={1000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                      </div>
                    )}
                    {isAdmin && status === "APPROVED" && (
                      <div className="space-y-3">
                        <label className="block text-sm text-slate-700">
                          Activation comments
                          <textarea
                            value={activationComment}
                            onChange={(event) => setActivationComment(event.target.value)}
                            rows={2}
                            maxLength={1000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                        <label className="flex items-start gap-2 text-sm text-slate-700">
                          <input
                            type="checkbox"
                            checked={activateConfirmed}
                            onChange={() => confirmAction(model, "activate")}
                          />
                          I explicitly confirm activation of this model version.
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            void performModelAction(
                              model,
                              "activate",
                              {
                                activation_confirmation: activateConfirmed,
                                activation_comment: activationComment,
                              },
                              (result) => `Activated ${result.model_version}.`,
                            )
                          }
                          disabled={saving || !activateConfirmed || activationComment.trim().length < 12}
                          className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                        >
                          Activate approved model
                        </button>
                      </div>
                    )}
                    {canRollback && (
                      <div className="space-y-3 border-t border-slate-200 pt-3">
                        <label className="block text-sm text-slate-700">
                          Rollback comments
                          <textarea
                            value={rollbackComment}
                            onChange={(event) => setRollbackComment(event.target.value)}
                            rows={2}
                            maxLength={1000}
                            className="mt-1 w-full rounded-lg border border-slate-300 p-2"
                          />
                        </label>
                        <label className="flex items-start gap-2 text-sm text-slate-700">
                          <input
                            type="checkbox"
                            checked={rollbackConfirmed}
                            onChange={() => confirmAction(model, "rollback")}
                          />
                          I explicitly confirm rollback to this previous model version.
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            void performModelAction(
                              model,
                              "rollback",
                              {
                                rollback_confirmation: rollbackConfirmed,
                                rollback_comment: rollbackComment,
                              },
                              (result) => `Rolled back to ${result.model_version}.`,
                            )
                          }
                          disabled={saving || !rollbackConfirmed || rollbackComment.trim().length < 12}
                          className="rounded-lg border border-amber-400 px-3 py-2 text-sm font-semibold text-amber-800 disabled:opacity-50"
                        >
                          Rollback to this version
                        </button>
                      </div>
                    )}
                  </div>
                </details>
              );
            })}
          </section>

          {isAdmin && (
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-900">Train a candidate model</h2>
              <p className="mt-1 text-sm text-slate-600">
                Upload authorized historical records only. Use a stable applicant ID when a person has multiple applications; application/source IDs are accepted and hashed for grouping. Workflow statuses are not visa outcomes.
              </p>
              <form className="mt-4 space-y-4" onSubmit={(event) => void trainModel(event)}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="text-sm font-medium text-slate-700">
                    Model type
                    <select
                      value={modelType}
                      onChange={(event) => changeModelType(event.target.value)}
                      className="mt-1 block w-full rounded-lg border border-slate-300 p-2"
                    >
                      <option value="visa">Visa outcome (Approved / Rejected)</option>
                      <option value="financial">Financial assessment (LOW / MEDIUM / HIGH)</option>
                    </select>
                  </label>
                  <label className="text-sm font-medium text-slate-700">
                    Algorithm
                    <select
                      value={algorithm}
                      onChange={(event) => setAlgorithm(event.target.value)}
                      className="mt-1 block w-full rounded-lg border border-slate-300 p-2"
                    >
                      <option value="lightgbm">LightGBM</option>
                      <option value="random_forest">Random Forest</option>
                      <option value="logistic_regression">Logistic regression (visa outcomes only)</option>
                    </select>
                  </label>
                  <label className="text-sm font-medium text-slate-700">
                    Dataset (CSV, XLS, XLSX)
                    <input
                      type="file"
                      accept=".csv,.xls,.xlsx"
                      onChange={(event) => {
                        setDataset(event.target.files?.[0] || null);
                        setDatasetValidation(null);
                      }}
                      className="mt-1 block w-full rounded-lg border border-slate-300 p-2 text-sm"
                      required
                    />
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => void downloadDatasetTemplate()}
                  disabled={downloadingTemplate}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 disabled:opacity-60"
                >
                  {downloadingTemplate ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <Download className="h-4 w-4" />
                  )}
                  Download CSV template
                </button>
                <label className="block text-sm font-medium text-slate-700">
                  Source labels to canonical labels (JSON)
                  <textarea
                    value={labelMapping}
                    onChange={(event) => {
                      setLabelMapping(event.target.value);
                      setDatasetValidation(null);
                    }}
                    rows={4}
                    spellCheck="false"
                    className="mt-1 w-full rounded-lg border border-slate-300 p-2 font-mono text-xs"
                    required
                  />
                </label>
                <label className="block text-sm font-medium text-slate-700">
                  Authorized data source (12–500 characters)
                  <input
                    value={sourceDescription}
                    onChange={(event) => setSourceDescription(event.target.value)}
                    minLength={12}
                    maxLength={500}
                    className="mt-1 block w-full rounded-lg border border-slate-300 p-2"
                    required
                  />
                </label>
                <label className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={verifiedOutcomes}
                    onChange={(event) => setVerifiedOutcomes(event.target.checked)}
                    required
                  />
                  These are real, verified outcomes, not synthetic data or workflow statuses.
                </label>
                <label className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={privacyReviewed}
                    onChange={(event) => setPrivacyReviewed(event.target.checked)}
                    required
                  />
                  Dataset use and privacy have been reviewed and authorized.
                </label>
                <p className="text-sm text-slate-600">
                  Dataset template and training instructions: docs/ML_BANK_STATEMENT_UPGRADE.md
                </p>
                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={() => void validateDataset()}
                    disabled={validatingDataset || saving || !dataset}
                    className="flex items-center gap-2 rounded-lg border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-800 disabled:opacity-60"
                  >
                    {validatingDataset ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <AlertCircle className="h-4 w-4" />
                    )}
                    Validate dataset
                  </button>
                  <button
                    type="submit"
                    disabled={
                      saving || !dataset || !datasetValidation?.ready_to_train
                    }
                    className="flex items-center gap-2 rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                  >
                    {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                    Train candidate
                  </button>
                </div>
              </form>
              {datasetValidation && (
                <div
                  role="status"
                  className={`mt-4 rounded-lg border p-4 text-sm ${
                    datasetValidation.ready_to_train
                      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                      : "border-amber-200 bg-amber-50 text-amber-950"
                  }`}
                >
                  <p className="font-semibold">
                    Dataset validation: {datasetValidation.validation_status}
                  </p>
                  <dl className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    <div>Total records: {datasetValidation.total_records}</div>
                    <div>Valid records: {datasetValidation.valid_records}</div>
                    <div>Invalid records: {datasetValidation.invalid_record_count}</div>
                    <div>Labeled records: {datasetValidation.labeled_records}</div>
                    {Object.entries(datasetValidation.class_counts || {}).map(
                      ([label, count]) => (
                        <div key={label}>
                          {label}: {count} records ·{" "}
                          {datasetValidation.group_counts?.[label] || 0} groups
                        </div>
                      ),
                    )}
                    <div>Missing labels: {datasetValidation.missing_labels}</div>
                    <div>
                      Duplicate records:{" "}
                      {datasetValidation.duplicate_records?.count || 0}
                    </div>
                  </dl>
                  <p className="mt-3">
                    Supported feature columns:{" "}
                    {datasetValidation.feature_columns?.join(", ") || "none"}
                  </p>
                  {(datasetValidation.missing_columns?.length > 0 ||
                    datasetValidation.messages?.length > 0) && (
                    <ul className="mt-3 list-disc space-y-1 pl-5">
                      {datasetValidation.missing_columns?.map((column) => (
                        <li key={`missing-${column}`}>Missing column: {column}</li>
                      ))}
                      {datasetValidation.messages?.map((message) => (
                        <li key={message}>{message}</li>
                      ))}
                    </ul>
                  )}
                  {datasetValidation.invalid_records?.length > 0 && (
                    <details className="mt-3">
                      <summary className="cursor-pointer font-medium">
                        Invalid records ({datasetValidation.invalid_record_count})
                      </summary>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {datasetValidation.invalid_records
                          .slice(0, 20)
                          .map((record) => (
                            <li key={record.row}>
                              Row {record.row}: {record.issues.join(" ")}
                            </li>
                          ))}
                      </ul>
                    </details>
                  )}
                </div>
              )}
            </section>
          )}

          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="space-y-3 text-sm text-slate-600">
              {["privateDocumentStorage", "jwtAuthorization", "rateLimiting", "languageSupport"].map((setting) => (
                <div key={setting} className="flex items-center justify-between">
                  <span>{t(setting)}</span>
                  <span>{t("enabled")}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
