import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, CheckCircle2, FileText } from "lucide-react";
import FileUpload from "./FileUpload";
import { getRequirementMaxFileSize } from "../pages/pageUtils";

/** Reusable requirements checklist; callers supply data and document actions. */
export default function RequirementChecklist({
  requirements = [],
  documents = [],
  onUpload,
  onRetryOcr,
  retryingOcrDocumentId,
}) {
  const { t, i18n } = useTranslation();
  const [checkedOverrides, setCheckedOverrides] = useState({});
  return (
    <div className="space-y-3">
      {requirements.map((requirement) => {
        const id = requirement.requirementId || requirement._id;
        const files = documents.filter(
          (file) => file.requirementId === id || file.requirement === id,
        );
        const currentFiles = files.filter(
          (file) =>
            !["DELETED", "REPLACED"].includes(
              String(file.status || "").toUpperCase(),
            ),
        );
        const currentDocument = currentFiles[0];
        const ocrStatus = String(currentDocument?.ocrStatus || "").toUpperCase();
        const isBankStatement =
          String(requirement.code || currentDocument?.requirementCode || "")
            .toLowerCase() === "bank_statement";
        const isPhoto =
          String(requirement.code || currentDocument?.requirementCode || "")
            .toLowerCase()
            .includes("photo");
        const ocrProgress = Math.min(
          100,
          Math.max(0, Number(currentDocument?.ocrProgressPercent) || 0),
        );
        const formats = requirement.acceptedFormats || [
          "pdf",
          "jpg",
          "jpeg",
          "png",
        ];
        const isChecked = checkedOverrides[id] ?? currentFiles.length > 0;
        const maxFileSize = getRequirementMaxFileSize(requirement);
        const isComplete = currentFiles.some(
          (file) => String(file.status || "").toUpperCase() !== "REJECTED",
        );
        const bankAnalysis = currentDocument?.bankStatementAnalysis;
        const bankScore =
          bankAnalysis?.computedBankScore ?? bankAnalysis?.bankStatementScore;
        const bankScoreAvailable =
          bankScore?.available &&
          bankScore.points != null &&
          Number.isFinite(Number(bankScore.points)) &&
          Number(bankScore.maximum) > 0;
        return (
          <article
            key={id}
            className="grid gap-3 p-4 transition hover:bg-blue-50/30 sm:grid-cols-[minmax(0,1fr)_minmax(170px,220px)_minmax(120px,170px)] sm:items-center"
          >
            <div className="flex min-w-0 items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-700">
                <FileText className="h-5 w-5" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <div className="font-semibold text-slate-900">
                  {requirement.name}
                  <span
                    className={`ml-2 inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${requirement.required ? "bg-red-50 text-red-700" : "bg-blue-50 text-blue-700"}`}
                  >
                    {requirement.required
                      ? t("required")
                      : requirement.conditional
                        ? t("conditionalRequirement")
                        : t("optional")}
                  </span>
                </div>
                {requirement.description && (
                  <p className="mt-1 text-sm text-slate-600">
                    {typeof requirement.description === "string"
                      ? requirement.description
                      : requirement.description[i18n.language] ||
                        requirement.description.en}
                  </p>
                )}
                <p className="mt-1 text-xs text-slate-500">
                  {t("acceptedFormats")}: {formats.join(", ").toUpperCase()} ·{" "}
                  {t("maximum")} {(maxFileSize / 1024 / 1024).toFixed(0)} MB ·{" "}
                  <span className="font-semibold text-blue-700">
                    {Number(requirement.weight ?? requirement.points ?? 0)} points
                  </span>
                </p>
                <label className="mt-2 inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-slate-600">
                  <input
                    type="checkbox"
                    checked={isChecked}
                    className="h-4 w-4 accent-blue-600"
                    onChange={(event) =>
                      setCheckedOverrides((current) => ({
                        ...current,
                        [id]: event.target.checked,
                      }))
                    }
                  />
                  {t("iHaveThisDocument")}
                </label>
              </div>
            </div>

            <div className="flex min-w-0 items-center gap-2 sm:justify-center">
              {isComplete ? (
                <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" aria-hidden="true" />
              ) : (
                <AlertCircle className="h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
              )}
              <div className="min-w-0">
                <p className={`text-sm font-semibold ${isComplete ? "text-emerald-700" : "text-slate-600"}`}>
                  {isComplete ? t("uploaded") : t("notUploaded")}
                </p>
                <p className="text-xs font-medium text-slate-500">
                  Status: {String(currentDocument?.status || "NOT_UPLOADED").toUpperCase()}
                </p>
                <p className="truncate text-xs text-slate-500">
                  {currentDocument?.fileName || "—"}
                </p>
                {currentDocument && (
                  <p
                    role={!isPhoto && ["OCR_PENDING", "OCR_PROCESSING"].includes(ocrStatus) ? "status" : undefined}
                    className={`mt-1 text-xs ${
                      isPhoto
                        ? "text-slate-600"
                        : ocrStatus === "OCR_FAILED"
                        ? "text-red-700"
                        : ocrStatus === "OCR_PARTIAL"
                          ? "text-red-700"
                        : ocrStatus === "OCR_COMPLETED"
                          ? "text-emerald-700"
                          : "text-blue-700"
                    }`}
                  >
                    {isPhoto
                      ? t("photoOcrNotRequired")
                      : ocrStatus === "OCR_COMPLETED"
                      ? t("ocrCompletedReady")
                      : ocrStatus === "OCR_FAILED"
                        ? currentDocument.ocrError || t("documentReadingFailed")
                        : ocrStatus === "OCR_PARTIAL"
                          ? currentDocument.ocrError ||
                            currentDocument.ocrMessage ||
                            t("documentReadingFailed")
                        : ocrStatus === "UPLOADED"
                          ? t("uploaded")
                          : t("readingDocument")}
                  </p>
                )}
                {isBankStatement && ocrStatus === "OCR_COMPLETED" && (
                  <p className="mt-1 text-xs font-medium text-blue-800">
                    {t("bankStatementFieldCheck")}:{" "}
                    {currentDocument.bankStatementCheckPercent === null ||
                    currentDocument.bankStatementCheckPercent === undefined
                      ? t("bankStatementCheckUnavailable")
                      : `${currentDocument.bankStatementCheckPercent}%`}
                  </p>
                )}
                {currentDocument && (
                  <div className="mt-2 rounded-lg border border-slate-200 bg-white p-2 text-xs">
                    <p className="font-semibold text-slate-800">
                      Status: {String(currentDocument.status || "PROCESSING").toUpperCase()}
                    </p>
                    <p className="mt-1 text-slate-700">
                      Score:{" "}
                      {isBankStatement
                        ? bankScoreAvailable
                          ? t("bankScore", {
                              points: Number(bankScore.points).toFixed(2),
                              maximum: bankScore.maximum,
                              percentage: (
                                (Number(bankScore.points) /
                                  Number(bankScore.maximum)) *
                                100
                              ).toFixed(2),
                            })
                          : t("bankScoreUnavailable", {
                              maximum: bankScore?.maximum ?? 45,
                            })
                        : `${currentDocument.awardedPoints ?? 0} / ${Number(requirement.weight ?? requirement.points ?? 0)} points`}
                    </p>
                    {currentDocument.automatedVerification && (
                      <>
                        <p className="mt-1 text-slate-600">
                          Automated checks: {currentDocument.automatedVerification.documentScore ?? "—"}/100
                        </p>
                        <p className="mt-1 text-slate-600">
                          Risk:{" "}
                          {currentDocument.automatedVerification.riskLevel ||
                            "UNAVAILABLE"}
                        </p>
                        {(currentDocument.automatedVerification.crossDocumentChecks || [])
                          .filter((check) => check.status !== "PASSED")
                          .map((check) => (
                            <p key={check.code} className="mt-1 text-amber-800">
                              {check.label}: {check.reason}
                            </p>
                          ))}
                        {!currentDocument.automatedVerification.crossDocumentChecks?.length &&
                          currentDocument.automatedVerification.reasons?.length > 0 && (
                            <p className="mt-1 text-amber-800">
                              {currentDocument.automatedVerification.reasons[0]}
                            </p>
                          )}
                      </>
                    )}
                  </div>
                )}
                {Number(currentDocument?.ocrTotalPages) > 0 && (
                  <>
                    <p className="mt-1 text-xs text-slate-600">
                      {t("ocrPagesProgress", {
                        processed: currentDocument.ocrPagesProcessed || 0,
                        total: currentDocument.ocrTotalPages,
                      })}
                      {" · "}
                      {t("ocrProgressPercent", { percent: ocrProgress })}
                    </p>
                    <div
                      className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100"
                      role="progressbar"
                      aria-valuenow={ocrProgress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={t("readingDocument")}
                    >
                      <div
                        className="h-full rounded-full bg-blue-600 transition-all"
                        style={{ width: `${ocrProgress}%` }}
                      />
                    </div>
                  </>
                )}
                {!isPhoto &&
                  ["OCR_FAILED", "OCR_PARTIAL", "UPLOADED"].includes(ocrStatus) &&
                  onRetryOcr && (
                  <button
                    type="button"
                    disabled={retryingOcrDocumentId === currentDocument._id}
                    onClick={() => onRetryOcr(currentDocument)}
                    className="mt-1 text-xs font-semibold text-blue-700 underline disabled:opacity-50"
                  >
                    {retryingOcrDocumentId === currentDocument._id
                      ? t("readingDocument")
                      : t("retryOcr")}
                  </button>
                  )}
              </div>
            </div>

            <div className="flex items-center justify-start gap-2 sm:justify-end">
              {isChecked && <FileUpload
                acceptedFormats={formats}
                maxFileSize={maxFileSize}
                disabled={!isChecked}
                label={currentDocument && !requirement.allowMultiple ? "replace" : "uploadDocument"}
                onUpload={(file, setProgress) =>
                  onUpload(
                    requirement,
                    file,
                    currentDocument && !requirement.allowMultiple
                      ? currentDocument._id
                      : undefined,
                    setProgress,
                  )
                }
                capture={requirement.code === "photo" ? "user" : undefined}
              />}
            </div>
          </article>
        );
      })}
    </div>
  );
}
