import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { CheckCircle2, Clock3, FileText, MapPin, Pencil } from "lucide-react";
import ApplicationCreator from "../components/ApplicationCreator";
import RequirementChecklist from "../components/RequirementChecklist";
import {
  api,
  getRequirementMaxFileSize,
  useSecureData,
  waitForDocumentOcr,
} from "./pageUtils";

export default function ApplicationsPage({ profile = {} }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { applicationId } = useParams();
  const isChecklistPage = Boolean(applicationId);
  const {
    data: initialApplications = [],
    loading: applicationsLoading,
    error: applicationsError,
  } = useSecureData(async () => {
    const response = await api.get("/visa-types/applications");
    return response.data.applications || [];
  }, []);
  const [createdApplications, setCreatedApplications] = useState([]);
  const [deletedApplicationIds, setDeletedApplicationIds] = useState([]);
  const applications = useMemo(() => {
    const byId = new Map(
      initialApplications.map((application) => [application._id, application]),
    );
    createdApplications.forEach((application) =>
      byId.set(application._id, application),
    );
    return [...byId.values()].filter(
      (application) => !deletedApplicationIds.includes(application._id),
    );
  }, [createdApplications, deletedApplicationIds, initialApplications]);
  const [customers, setCustomers] = useState([]);
  const [visaTypes, setVisaTypes] = useState([]);
  const [error, setError] = useState("");
  const [selectedApplication, setSelectedApplication] = useState(null);
  const [requirements, setRequirements] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [retryingOcrDocumentId, setRetryingOcrDocumentId] = useState("");
  const [verificationResult, setVerificationResult] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [checklistLoading, setChecklistLoading] = useState(false);
  const [checklistLoadError, setChecklistLoadError] = useState("");
  const loadedChecklistId = useRef("");
  const verificationInFlight = useRef(new Set());

  const selectedVisaType = visaTypes.find(
    (item) =>
      item._id ===
      (selectedApplication?.visaType || selectedApplication?.visaTypeId),
  );
  const activeRequirements = requirements.length
    ? requirements
    : selectedApplication?.requirementSnapshot || [];
  const isCurrentDocument = (document) =>
    !["REJECTED", "DELETED", "REPLACED"].includes(
      String(document.status || "").toUpperCase(),
    );
  const requirementDocuments = (requirementId) =>
    documents.filter(
      (document) =>
        document.requirementId === requirementId ||
        document.requirement === requirementId,
    );
  const isPhotoDocument = (document) => {
    const requirement = activeRequirements.find(
      (item) =>
        String(item.requirementId || item._id) ===
        String(document.requirementId || document.requirement),
    );
    return String(
      document.requirementCode || requirement?.code || requirement?.name || "",
    )
      .toLowerCase()
      .includes("photo");
  };
  const missingRequired = activeRequirements.filter(
    (requirement) =>
      requirement.required &&
      !requirementDocuments(requirement.requirementId || requirement._id).some(
        isCurrentDocument,
      ),
  );
  const completedCount = activeRequirements.filter((requirement) =>
    requirementDocuments(requirement.requirementId || requirement._id).some(
      isCurrentDocument,
    ),
  ).length;
  const completion = activeRequirements.length
    ? Math.round((completedCount / activeRequirements.length) * 100)
    : 0;
  const uploadedDocumentCount = documents.filter(isCurrentDocument).length;
  const latestUpdatedAt = documents
    .map(
      (document) =>
        document.updatedAt || document.createdAt || document.uploadDate,
    )
    .filter(Boolean)
    .map((date) => new Date(date))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((left, right) => right.getTime() - left.getTime())[0];
  const lastUpdated = latestUpdatedAt
    ? new Intl.DateTimeFormat(i18n.language === "kh" ? "km-KH" : "en-US", {
        dateStyle: "medium",
      }).format(latestUpdatedAt)
    : "—";
  const documentsNeedingOcr = documents.filter(
    (document) =>
      !["DELETED", "REPLACED"].includes(
        String(document.status || "").toUpperCase(),
      ) &&
      !isPhotoDocument(document) &&
      String(document.ocrStatus || "").toUpperCase() !== "OCR_COMPLETED",
  );
  const pendingOcrDocumentIds = documents
    .filter((document) => {
      const ocrStatus = String(document.ocrStatus || "").toUpperCase();
      return (
        ["OCR_PENDING", "OCR_PROCESSING"].includes(ocrStatus) ||
        (ocrStatus === "OCR_COMPLETED" &&
          String(document.status || "").toUpperCase() === "PROCESSING" &&
          !document.automatedVerification)
      );
    })
    .map((document) => document._id)
    .join(",");
  useEffect(() => {
    Promise.all([
      api.get("/customers"),
      api.get("/visa-types/types", { params: { active: true } }),
    ])
      .then(([customersResponse, visaTypesResponse]) => {
        setCustomers(customersResponse.data.customers || []);
        setVisaTypes(visaTypesResponse.data.visaTypes || []);
      })
      .catch((requestError) =>
        setError(
          requestError?.response?.data?.message || t("unableLoadOptions"),
        ),
      );
  }, [t]);

  useEffect(() => {
    if (!pendingOcrDocumentIds) return undefined;
    const active = { current: true };
    const timer = { current: undefined };
    const pollProcessing = async () => {
      const results = await Promise.all(
        pendingOcrDocumentIds.split(",").map(async (documentId) => {
          try {
            const response = await api.get(
              `/documents/${documentId}/processing`,
            );
            return { documentId, progress: response.data };
          } catch (requestError) {
            return { documentId, error: requestError };
          }
        }),
      );
      if (!active.current) return;
      const failedRequest = results.find((result) => result.error);
      if (failedRequest) {
        setError(
          failedRequest.error?.response?.data?.message ||
            failedRequest.error?.message ||
            t("unableLoadChecklist"),
        );
      }
      setDocuments((current) =>
        current.map((document) => {
          const result = results.find(
            (item) => item.documentId === document._id,
          );
          if (!result?.progress) return document;
          return {
            ...document,
            workflowStatus: result.progress.workflowStatus,
            ocrStatus: result.progress.ocrStatus,
            ocrPagesProcessed: result.progress.pagesProcessed,
            ocrTotalPages: result.progress.totalPages,
            ocrProgressPercent: result.progress.progressPercent,
            ocrMessage: result.progress.message,
            ocrError: result.progress.error,
            bankStatementCheckPercent:
              result.progress.bankStatementCheckPercent,
            bankStatementMissingFields:
              result.progress.bankStatementMissingFields,
            bankStatementAnalysis:
              result.progress.bankStatementAnalysis ||
              document.bankStatementAnalysis,
            status: result.progress.status || document.status,
            automatedVerification:
              result.progress.automatedVerification ||
              document.automatedVerification,
          };
        }),
      );
      timer.current = window.setTimeout(pollProcessing, 750);
    };
    void pollProcessing();
    return () => {
      active.current = false;
      window.clearTimeout(timer.current);
    };
  }, [pendingOcrDocumentIds, t]);

  const loadChecklist = useCallback(
    async (application) => {
      setChecklistLoading(true);
      setChecklistLoadError("");
      setSelectedApplication(application);
      setRequirements([]);
      setDocuments([]);
      setVerificationResult(null);
      try {
        const [requirementsResponse, documentsResponse] = await Promise.all([
          api.get("/requirements", {
            params: {
              visaTypeId: application.visaType || application.visaTypeId,
              countryId: application.countryId,
            },
          }),
          api.get("/documents", { params: { applicationId: application._id } }),
        ]);
        const pinned = application.evidencePolicySnapshot?.requirements;
        setRequirements(pinned ? pinned.map(r=>({...r,_id:r.requirementId||r._id})) : requirementsResponse.data.requirements || []);
        setDocuments(documentsResponse.data.documents || []);
        const historyResponse = await api.get("/verification", {
          params: { applicationId: application._id },
        });
        const latestVerification = (
          historyResponse.data.verifications || []
        ).sort(
          (left, right) =>
            new Date(right.createdAt).getTime() -
            new Date(left.createdAt).getTime(),
        )[0];
        setError("");
        setSelectedApplication(application);
        setVerificationResult(latestVerification || null);
        return true;
      } catch (requestError) {
        const message =
          requestError?.response?.data?.message || t("unableLoadChecklist");
        setError(message);
        setChecklistLoadError(message);
        return false;
      } finally {
        setChecklistLoading(false);
      }
    },
    [t],
  );

  const runVerificationForApplication = useCallback(
    async (application) => {
      if (!application) return null;
      if (verificationInFlight.current.has(application._id)) return null;
      verificationInFlight.current.add(application._id);
      setVerifying(true);
      setError("");
      try {
        const response = await api.post("/verification/run", {
          applicationId: application._id,
        });
        const verification = response.data.verification;
        setVerificationResult(verification);
        const updated = {
          ...application,
          hasVerificationResult: true,
          documentReadiness: verification?.documentReadiness,
          verificationPercentage: verification?.verificationPercentage,
          status:
            verification?.status === "MISSING" ? "DRAFT" : "UNDER_VERIFICATION",
          completenessScore:
            verification?.completenessScore ?? application.completenessScore,
          earnedPoints: verification?.earnedPoints,
          maximumPoints: verification?.maximumPoints,
          lastCalculationAt: verification?.lastCalculationAt,
        };
        setSelectedApplication(updated);
        setCreatedApplications((current) => [
          updated,
          ...current.filter((item) => item._id !== updated._id),
        ]);
        return verification;
      } catch (requestError) {
        setError(
          requestError?.response?.data?.message ||
            t("unableSubmitVerification"),
        );
        return null;
      } finally {
        verificationInFlight.current.delete(application._id);
        setVerifying(false);
      }
    },
    [t],
  );

  useEffect(() => {
    if (!applicationId) {
      loadedChecklistId.current = "";
      return;
    }
    if (applicationsLoading || loadedChecklistId.current === applicationId) {
      return;
    }
    const application =
      location.state?.application?._id === applicationId
        ? location.state.application
        : applications.find((item) => item._id === applicationId);
    if (!application) {
      loadedChecklistId.current = applicationId;
      return;
    }
    loadedChecklistId.current = applicationId;
    void Promise.resolve()
      .then(() => loadChecklist(application))
      .then(async (loaded) => {
        if (loaded && location.state?.verifyOnOpen) {
          await runVerificationForApplication(application);
          navigate(location.pathname, {
            replace: true,
            state: { application },
          });
        }
      });
  }, [
    applicationId,
    applications,
    applicationsLoading,
    location.pathname,
    location.state,
    loadChecklist,
    navigate,
    runVerificationForApplication,
    t,
  ]);

  const removeApplication = async (application) => {
    if (
      profile.role !== "ADMIN" ||
      !window.confirm(
        t("confirmDeleteApplication", {
          applicationNumber: application.applicationNumber,
        }),
      )
    ) {
      return;
    }
    try {
      await api.delete(`/visa-types/applications/${application._id}`);
      setDeletedApplicationIds((current) =>
        current.includes(application._id)
          ? current
          : [...current, application._id],
      );
      setCreatedApplications((current) =>
        current.filter((item) => item._id !== application._id),
      );
      if (selectedApplication?._id === application._id) {
        setSelectedApplication(null);
        setRequirements([]);
        setDocuments([]);
        setVerificationResult(null);
      }
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || t("unableDeleteApplication"),
      );
    }
  };

  const uploadFile = async (
    requirement,
    file,
    replaceDocumentId,
    setUploadProgress,
  ) => {
    if (!file || !selectedApplication) return;
    const formats = requirement.acceptedFormats || [
      "pdf",
      "jpg",
      "jpeg",
      "png",
    ];
    const extension = file.name.split(".").pop()?.toLowerCase();
    const maxFileSize = getRequirementMaxFileSize(requirement);
    if (
      (!formats.includes(extension) &&
        !(extension === "jpg" && formats.includes("jpeg")) &&
        !(extension === "jpeg" && formats.includes("jpg"))) ||
      file.size > maxFileSize
    ) {
      setError(
        `${t("useAcceptedFile")} (${formats.join(", ")}) ${t("upTo")} ${(maxFileSize / 1024 / 1024).toFixed(1)} ${t("mb")}.`,
      );
      return;
    }
    setError("");
    const payload = new FormData();
    payload.append("file", file);
    payload.append("applicationId", selectedApplication._id);
    payload.append(
      "requirementId",
      requirement.requirementId || requirement._id,
    );
    if (replaceDocumentId) {
      payload.append("replaceDocumentId", replaceDocumentId);
    }
    try {
      const response = await api.post("/documents/upload", payload, {
        onUploadProgress: (event) => {
          if (!event.total) return;
          setUploadProgress?.(Math.round((event.loaded / event.total) * 90));
        },
      });
      setUploadProgress?.(95);
      const uploadedDocument = response.data.document;
      setDocuments((current) => [
        ...current.map((document) =>
          document._id === replaceDocumentId
            ? { ...document, status: "REPLACED" }
            : document,
        ),
        uploadedDocument,
      ]);
      void waitForDocumentOcr(api, uploadedDocument._id, (progress) => {
        setDocuments((current) =>
          current.map((document) =>
            document._id === uploadedDocument._id
              ? {
                  ...document,
                  workflowStatus: progress.workflowStatus,
                  ocrStatus: progress.ocrStatus,
                  ocrPagesProcessed: progress.pagesProcessed,
                  ocrTotalPages: progress.totalPages,
                  ocrProgressPercent: progress.progressPercent,
                  ocrMessage: progress.message,
                  ocrError: progress.error,
                  bankStatementCheckPercent: progress.bankStatementCheckPercent,
                  bankStatementMissingFields:
                    progress.bankStatementMissingFields,
                  bankStatementAnalysis:
                    progress.bankStatementAnalysis ||
                    document.bankStatementAnalysis,
                  status: progress.status || document.status,
                  automatedVerification:
                    progress.automatedVerification ||
                    document.automatedVerification,
                }
              : document,
          ),
        );
      })
        .then(() => runVerificationForApplication(selectedApplication))
        .catch((requestError) =>
          setError(
            requestError?.response?.data?.message ||
              requestError?.message ||
              t("documentReadingFailed"),
          ),
        );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          requestError?.message ||
          t("uploadFailed"),
      );
    }
  };

  const retryDocumentOcr = async (document) => {
    if (retryingOcrDocumentId) return;
    setRetryingOcrDocumentId(document._id);
    try {
      const response = await api.post(`/documents/${document._id}/ocr/retry`);
      setDocuments((current) =>
        current.map((item) =>
          item._id === document._id
            ? { ...item, ...response.data.document }
            : item,
        ),
      );
      await waitForDocumentOcr(api, document._id, (progress) => {
        setDocuments((current) =>
          current.map((item) =>
            item._id === document._id
              ? {
                  ...item,
                  workflowStatus: progress.workflowStatus,
                  ocrStatus: progress.ocrStatus,
                  ocrPagesProcessed: progress.pagesProcessed,
                  ocrTotalPages: progress.totalPages,
                  ocrProgressPercent: progress.progressPercent,
                  ocrMessage: progress.message,
                  ocrError: progress.error,
                  bankStatementCheckPercent: progress.bankStatementCheckPercent,
                  bankStatementMissingFields:
                    progress.bankStatementMissingFields,
                  bankStatementAnalysis:
                    progress.bankStatementAnalysis ||
                    item.bankStatementAnalysis,
                  status: progress.status || item.status,
                  automatedVerification:
                    progress.automatedVerification ||
                    item.automatedVerification,
                }
              : item,
          ),
        );
      });
      await runVerificationForApplication(selectedApplication);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          requestError?.message ||
          t("documentReadingFailed"),
      );
    } finally {
      setRetryingOcrDocumentId("");
    }
  };
  const verificationExplanation = (result) => {
    const message = result.explanationEn || "";
    if (i18n.language !== "kh") return message || t("manualCheckExplanation");
    if (
      /automated completeness check does not confirm authenticity/i.test(
        message,
      )
    ) {
      return t("automatedDocumentCheckExplanation");
    }
    const localized = [
      [/passport date of birth does not match/i, "passportDateMismatch"],
      [/passport must remain valid/i, "passportExpiryInvalid"],
      [/bank statement must cover/i, "bankPeriodInvalid"],
      [/closing balance is below usd 4,000/i, "bankBalanceLow"],
      [
        /enter the opening balance.*all 3 statement months/i,
        "bankSummaryMissing",
      ],
      [/complete all non-negative monthly/i, "bankSummaryInvalid"],
      [
        /monthly transaction totals do not reconcile/i,
        "bankReconciliationReview",
      ],
      [
        /declining balance over the 3-month period/i,
        "bankBalanceDeclineReview",
      ],
      [/fixed deposit is below usd 4,000/i, "fixedDepositAmountLow"],
    ].find(([pattern]) => pattern.test(message));
    return localized
      ? t(localized[1])
      : result.explanationKh || message || t("manualCheckExplanation");
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">
            {t(isChecklistPage ? "requirementsChecklist" : "applications")}
          </h1>
        </div>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-xl bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
          {checklistLoadError && selectedApplication && (
            <button
              type="button"
              onClick={() => {
                loadedChecklistId.current = applicationId;
                void loadChecklist(selectedApplication);
              }}
              disabled={checklistLoading}
              className="ml-3 font-semibold underline disabled:opacity-50"
            >
              {checklistLoading ? t("loading") : t("retry")}
            </button>
          )}
        </p>
      )}
      {!isChecklistPage && (
        <ApplicationCreator
          customers={customers}
          visaTypes={visaTypes}
          api={api}
          verificationResult={verificationResult}
          verifying={verifying}
          verificationExplanation={verificationExplanation}
          onApplicationCreated={async (application, customer) => {
            setCustomers((current) => [
              customer,
              ...current.filter((item) => item._id !== customer._id),
            ]);
            setCreatedApplications((current) => [
              application,
              ...current.filter((item) => item._id !== application._id),
            ]);
          }}
          onApplicationReady={async (application) => {
            setVerificationResult(null);
            const loaded = await loadChecklist(application);
            if (loaded) await runVerificationForApplication(application);
          }}
          onDocumentUploaded={(document) =>
            setDocuments((current) => [
              ...current.filter((item) => item._id !== document._id),
              document,
            ])
          }
        />
      )}

      {isChecklistPage && selectedApplication && (
        <section className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              {
                label: t("totalDocuments"),
                value: activeRequirements.length,
                detail: t("checklistItems"),
                Icon: FileText,
                color: "text-blue-700 bg-blue-50",
              },
              {
                label: t("documentCompletion"),
                value: `${completion}%`,
                detail: `${completedCount} ${t("of")} ${activeRequirements.length} ${t("completed")}`,
                Icon: CheckCircle2,
                color: "text-emerald-700 bg-emerald-50",
              },
              {
                label: t("lastUpdated"),
                value: lastUpdated,
                detail: `${uploadedDocumentCount} ${t("uploadedDocuments").toLowerCase()}`,
                Icon: Clock3,
                color: "text-violet-700 bg-violet-50",
              },
              {
                label: t("destination"),
                value:
                  selectedApplication.destination ||
                  selectedVisaType?.destination ||
                  "—",
                detail:
                  selectedVisaType?.name ||
                  selectedApplication.visaCategory ||
                  "",
                Icon: MapPin,
                color: "text-orange-700 bg-orange-50",
              },
            ].map(({ label, value, detail, Icon, color }) => (
              <article
                key={label}
                className="flex min-w-0 items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <span
                  className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${color}`}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-slate-500">{label}</p>
                  <p className="truncate text-lg font-bold text-slate-900">
                    {value}
                  </p>
                  <p className="truncate text-xs text-slate-500">{detail}</p>
                </div>
              </article>
            ))}
          </div>

          <div className="flex flex-col gap-4 rounded-2xl bg-gradient-to-r from-blue-700 to-indigo-700 p-5 text-white shadow-sm sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="rounded-xl bg-white/15 p-2.5">
                <FileText className="h-6 w-6" aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-xl font-bold">
                  {t("visaDocumentChecklist")}
                </h2>
                <p className="mt-1 max-w-2xl text-sm text-blue-100">
                  {t("checklistDescription")}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => navigate("/applications")}
              className="shrink-0 rounded-lg border border-white/40 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10"
            >
              {t("backToApplications")}
            </button>
          </div>

          <div className="space-y-5">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5">
                <div>
                  <h3 className="text-lg font-bold text-slate-900">
                    {t("requirementsChecklist")}
                  </h3>
                  <p className="mt-1 text-sm text-slate-500">
                    {t("checklistItemsCount", {
                      count: activeRequirements.length,
                    })}
                  </p>
                </div>
                <span className="rounded-full bg-blue-50 px-3 py-1 text-sm font-semibold text-blue-700">
                  {completedCount} / {activeRequirements.length} {t("uploaded")}
                </span>
              </div>
              {missingRequired.length > 0 && (
                <div className="mx-5 mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                  {t("missingRequiredPrefix")}{" "}
                  {missingRequired.map((item) => item.name).join(", ")}
                </div>
              )}
              {activeRequirements.length > 0 ? (
                <RequirementChecklist
                  requirements={activeRequirements}
                  documents={documents}
                  onUpload={uploadFile}
                  onRetryOcr={retryDocumentOcr}
                  retryingOcrDocumentId={retryingOcrDocumentId}
                />
              ) : (
                <p className="p-5 text-sm text-slate-500">
                  {t("noVisaRequirements")}
                </p>
              )}
              <div className="flex justify-end border-t border-slate-200 p-5">
                {documentsNeedingOcr.length > 0 && (
                  <p role="status" className="mr-auto text-sm text-amber-800">
                    {t("ocrVerificationBlocked")}
                  </p>
                )}
                <button
                  type="button"
                  disabled={
                    verifying ||
                    activeRequirements.length === 0 ||
                    documentsNeedingOcr.length > 0
                  }
                  onClick={() =>
                    runVerificationForApplication(selectedApplication)
                  }
                  className="rounded-xl bg-blue-700 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {verifying
                    ? t("verificationRunning")
                    : t("submitForVerification")}
                </button>
              </div>
            </section>
          </div>
          {verifying && (
            <p role="status" className="mt-5 text-right text-sm text-blue-700">
              {t("verificationRunning")}
            </p>
          )}
        </section>
      )}

      {isChecklistPage &&
        !selectedApplication &&
        !error &&
        applicationsLoading && (
          <p
            role="status"
            className="rounded-xl bg-slate-50 p-4 text-slate-600"
          >
            {t("loadingChecklist")}
          </p>
        )}
      {isChecklistPage && checklistLoading && (
        <p role="status" className="rounded-xl bg-slate-50 p-4 text-slate-600">
          {t("loadingChecklist")}
        </p>
      )}
      {isChecklistPage && !selectedApplication && applicationsError && (
        <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
          {applicationsError}
        </p>
      )}
      {isChecklistPage &&
        !selectedApplication &&
        !error &&
        !applicationsLoading && (
          <p role="alert" className="rounded-xl bg-amber-50 p-4 text-amber-800">
            {t("applicationNotFound")}
          </p>
        )}

      {!isChecklistPage && (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          {applications.length === 0 ? (
            <p className="p-6 text-sm text-slate-500">{t("noApplications")}</p>
          ) : (
            applications.map((application, index) => (
              <article
                key={application._id}
                className={`flex flex-col gap-4 p-5 md:flex-row md:items-center md:justify-between ${
                  index < applications.length - 1
                    ? "border-b border-slate-200"
                    : ""
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="font-semibold text-slate-900">
                      {customers.find(
                        (customer) => customer._id === application.customer,
                      )?.fullName ||
                        application.applicationNumber ||
                        "VD-1000"}
                    </div>
                    <span className="rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">
                      {t(
                        {
                          DRAFT: "draft",
                          UNDER_VERIFICATION: "underVerification",
                          PENDING: "pending",
                          VERIFIED: "verified",
                          REJECTED: "rejected",
                        }[application.status] ||
                          application.status ||
                          "underVerification",
                      )}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {application.applicationNumber || "VD-1000"}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-600">
                    <span>
                      {application.visaTypeName ||
                        application.visaCategory ||
                        application.destination}
                    </span>
                    {application.hasVerificationResult && (
                      <>
                        <span>
                          {t("documentReadiness")}{" "}
                          {application.documentReadiness}%
                        </span>
                        <span>
                          {t("documentCheck")}{" "}
                          {application.verificationPercentage}%
                        </span>
                      </>
                    )}
                    {application.approvalProbability != null && (
                      <span>
                        Approval estimate {application.approvalProbability}%
                      </span>
                    )}
                    <span>
                      Updated{" "}
                      {application.updatedAt
                        ? new Date(application.updatedAt).toLocaleDateString()
                        : "—"}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center justify-between gap-5 md:justify-end">
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/applications/${application._id}/results`)
                    }
                    className="text-sm font-medium text-indigo-700 hover:text-indigo-900"
                  >
                    Verification report
                  </button>
                  {application.receiptNumber && (
                    <button
                      type="button"
                      onClick={() =>
                        navigate(`/applications/${application._id}/results`)
                      }
                      className="text-sm font-medium text-emerald-700 hover:text-emerald-900"
                    >
                      Receipt
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/applications/${application._id}/checklist`, {
                        state: { application },
                      })
                    }
                    className="inline-flex items-center gap-1.5 text-sm font-medium text-blue-700 hover:text-blue-900"
                  >
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                    {t("edit")}
                  </button>
                  {profile.role === "ADMIN" && (
                    <button
                      type="button"
                      onClick={() => removeApplication(application)}
                      className="text-sm font-medium text-red-600 hover:text-red-800"
                    >
                      {t("deleteApplication")}
                    </button>
                  )}
                </div>
              </article>
            ))
          )}
        </section>
      )}
    </div>
  );
}
