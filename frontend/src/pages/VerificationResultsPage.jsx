import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  CircleCheck,
  CircleX,
  Clock3,
  Download,
  FileText,
  FolderOpen,
  Hourglass,
  Percent,
  Printer,
  ShieldAlert,
  Eye,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "./pageUtils";
import AbaBankPanel from "../components/AbaBankPanel";
import {
  AutomatedVerificationReport,
  BankStatementAnalysis,
  Metric,
} from "../components/VerificationReportDetails";

function isPhotoDocument(document) {
  return String(document.requirementCode || "").toLowerCase().includes("photo");
}

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

export default function VerificationResultsPage({ profile = {} }) {
  const { applicationId } = useParams();
  const [results, setResults] = useState(null);
  const [documentThumbnails, setDocumentThumbnails] = useState({});
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [documentPdfPreview, setDocumentPdfPreview] = useState({ documentId: "", url: "", error: "" });
  const [isDocumentPreviewOpen, setIsDocumentPreviewOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewForms, setReviewForms] = useState({});
  const canManage = ["ADMIN", "STAFF"].includes(profile.role);

  const loadResults = async () => {
    const response = await api.get(`/verification/results/${applicationId}`);
    setResults(response.data);
  };
  const selectedDocument = results?.documents?.find(
    (item) => item.documentId === selectedDocumentId,
  ) || results?.documents?.[0];
  const selectedDocumentContentUrl = selectedDocument?.contentUrl;
  const selectedDocumentMimeType = selectedDocument?.mimeType;
  const selectedDocumentPreviewId = selectedDocument?.documentId;

  useEffect(() => {
    let active = true;
    api
      .get(`/verification/results/${applicationId}`)
      .then((response) => {
        if (active) setResults(response.data);
      })
      .catch((requestError) => {
        if (active) {
          setError(
            requestError?.response?.data?.message ||
              "Unable to load verification results.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [applicationId]);

  useEffect(() => {
    const needsRefresh = results?.documents?.some((item) => {
      const status = String(item.ocrStatus || "").toUpperCase();
      return (
        ["OCR_PENDING", "OCR_PROCESSING"].includes(status) ||
        (status === "OCR_COMPLETED" && !item.automatedVerification)
      );
    });
    if (!needsRefresh) return undefined;
    const timer = window.setInterval(() => {
      api
        .get(`/verification/results/${applicationId}`)
        .then((response) => setResults(response.data))
        .catch((requestError) =>
          setError(
            requestError?.response?.data?.message ||
              "Unable to refresh automated verification results.",
          ),
        );
    }, 1500);
    return () => window.clearInterval(timer);
  }, [applicationId, results]);

  useEffect(() => {
    if (!results?.documents?.length) return undefined;
    let active = true;
    const objectUrls = [];
    Promise.all(
      results.documents.filter((document) => document.mimeType?.startsWith("image/")).map(async (document) => {
        try {
          const response = await api.get(document.contentUrl, {
            responseType: "blob",
          });
          const url = URL.createObjectURL(response.data);
          objectUrls.push(url);
          return [document.documentId, url];
        } catch {
          return [document.documentId, ""];
        }
      }),
    ).then((items) => {
      if (!active) return;
      setDocumentThumbnails(Object.fromEntries(items));
    });
    return () => {
      active = false;
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [results]);

  useEffect(() => {
    if (
      selectedDocumentMimeType !== "application/pdf"
      || !selectedDocumentPreviewId
      || !selectedDocumentContentUrl
    ) {
      return undefined;
    }

    const controller = new AbortController();
    let objectUrl = "";
    api.get(selectedDocumentContentUrl, {
      responseType: "blob",
      signal: controller.signal,
    })
      .then((response) => {
        objectUrl = URL.createObjectURL(response.data);
        setDocumentPdfPreview({ documentId: selectedDocumentPreviewId, url: objectUrl, error: "" });
      })
      .catch((requestError) => {
        if (!controller.signal.aborted) {
          setDocumentPdfPreview({
            documentId: selectedDocumentPreviewId,
            url: "",
            error: requestError?.response?.data?.message || "Document preview could not be loaded.",
          });
        }
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    selectedDocumentPreviewId,
    selectedDocumentContentUrl,
    selectedDocumentMimeType,
  ]);

  const downloadReceiptNumber = async (number) => {
    if (typeof number !== "string" || !number.trim()) return;
    setError("");
    try {
      const response = await api.get(
        `/verification/receipts/${encodeURIComponent(number)}/pdf`,
        { responseType: "blob" },
      );
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${number}.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to download the receipt.",
      );
    }
  };

  // Keep DOM click events out of the receipt-number argument. Both current
  // and historical downloads use the same authenticated blob request.
  const downloadReceipt = () => downloadReceiptNumber(results?.receiptNumber);

  const rerunDocumentVerification = async (documentId) => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/documents/${documentId}/verification/retry`);
      await loadResults();
      setNotice("Document verification was rerun using the saved OCR evidence.");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to rerun document verification.",
      );
    } finally {
      setBusy(false);
    }
  };

  const reviewDocument = async (item) => {
    const form = reviewForms[item.documentId] || {};
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.patch(`/documents/${item.documentId}/review`, {
        status: form.status || "NEEDS_REVIEW",
        reason: form.reason || "",
      });
      await loadResults();
      setReviewForms((current) => ({
        ...current,
        [item.documentId]: { ...current[item.documentId], reason: "" },
      }));
      setNotice("Document review override saved with its audit reason.");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to save the document review.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!results) {
    return (
      <main className="space-y-4">
        <h1 className="text-2xl font-bold text-slate-900">
          Verification Report
        </h1>
        {error ? (
          <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
            {error}
          </p>
        ) : (
          <p role="status" className="text-slate-600">
            Loading verification report…
          </p>
        )}
      </main>
    );
  }

  const application = results.application || {};
  const applicant = results.applicant || {};
  const summary = results.summary || {};
  const readiness = summary.readinessScore;
  const bankRequirementScores = Object.fromEntries(
    (summary.bankStatementScore || []).map((item) => [item.documentId, item]),
  );
  const documents = results.documents || [];
  const latestVerification = results.latestVerification;
  const report = results.verificationReport || {};
  const receiptNumber = results.receiptNumber;
  const finalized = Boolean(receiptNumber) && results.receiptIsCurrent !== false;
  const reportStatus = finalized
    ? report.finalStatus || application.status || "RECORDED"
    : summary.automatedVerificationStatus || "INSUFFICIENT_DATA";
  const statusStyle =
    reportStatus === "VERIFIED"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : reportStatus === "REJECTED"
        ? "border-red-200 bg-red-50 text-red-800"
        : "border-amber-200 bg-amber-50 text-amber-900";
  const allChecks = documents.flatMap((item) =>
    (item.automatedVerification?.checks || []).map((check) => ({
      ...check,
      fileName: item.fileName,
      documentStatus: item.status,
    })),
  );
  const passedChecks = allChecks.filter((check) => check.status === "PASSED");
  const unresolvedChecks = allChecks.filter(
    (check) => check.status !== "PASSED" && String(check.documentStatus || "").toUpperCase() !== "VERIFIED",
  );
  const terminalDocumentStates = [
    "VERIFIED",
    "REJECTED",
    "SUSPICIOUS",
    "INSUFFICIENT_DATA",
  ];
  const pendingDocuments = documents.filter(
    (item) =>
      !terminalDocumentStates.includes(String(item.status || "").toUpperCase()),
  ).length;
  const totalPages = summary.ocrProgress?.totalPages ?? 0;
  const processedPages = summary.ocrProgress?.pagesProcessed ?? 0;
  const reportDate = latestVerification?.createdAt || application.createdAt;
  const printReport = () => {
    const invoiceReference =
      receiptNumber ||
      application.applicationNumber ||
      `INV-${String(applicationId || "").slice(-8).toUpperCase()}`;
    const issueDate = reportDate
      ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "2-digit" }).format(new Date(reportDate))
      : "Not recorded";
    const dueDate = reportDate
      ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "2-digit" }).format(
          new Date(new Date(reportDate).getTime() + 7 * 86400000),
        )
      : "Not recorded";
    const verificationPercent = Math.round(Number(summary.documentReadiness ?? summary.verificationPercentage ?? 0));
    const verificationStatus = reportStatus === "VERIFIED" ? "Verified" : "Pending Review";
    const verificationRows = requirementScores.length
      ? requirementScores.map((item, index) => {
          const status = statusMeta(item.status).label;
          const score = `${item.earnedPoints ?? 0} / ${item.configuredPoints ?? 0}`;
          return `<tr>
            <td>${index + 1}</td>
            <td>${escapeHtml(item.name || "Document")}</td>
            <td><span class="${status === "Verified" ? "ok" : "pending"}">● ${escapeHtml(status)}</span></td>
            <td>${escapeHtml(score)}</td>
            <td>${status === "Verified" ? "Requirements complete" : "Needs review"}</td>
          </tr>`;
        }).join("")
      : documents.map((item, index) => `<tr>
          <td>${index + 1}</td>
          <td>${escapeHtml(documentName(item))}</td>
          <td><span class="${String(item.status).toUpperCase() === "VERIFIED" ? "ok" : "pending"}">● ${escapeHtml(statusMeta(item.status).label)}</span></td>
          <td>${escapeHtml(`${item.awardedPoints ?? 0} / ${item.assignedPoints ?? 0}`)}</td>
          <td>${escapeHtml(item.verificationComments || item.rejectionReason || "—")}</td>
        </tr>`).join("");
    const approved = reportStatus === "VERIFIED";
    const printFrame = document.createElement("iframe");
    printFrame.title = "Invoice print";
    printFrame.setAttribute("aria-hidden", "true");
    Object.assign(printFrame.style, {
      position: "fixed",
      width: "1px",
      height: "1px",
      right: "0",
      bottom: "0",
      border: "0",
      opacity: "0",
      pointerEvents: "none",
    });
    document.body.appendChild(printFrame);
    const printDocument =
      printFrame.contentDocument || printFrame.contentWindow?.document;
    if (!printDocument) {
      printFrame.remove();
      setError("Unable to prepare the invoice for printing.");
      return;
    }
    printDocument.open();
    printDocument.write(`<!doctype html>
      <html>
        <head>
          <meta charset="utf-8" />
          <title>${escapeHtml(invoiceReference)}</title>
          <style>
            @page { size: A4; margin: 8mm; }
            * { box-sizing: border-box; }
            body { margin: 0; color: #26344d; font-family: Arial, sans-serif; font-size: 10px; background: #fff; }
            .sheet { width: 100%; height: 277mm; overflow: hidden; border: 1px solid #dfe7f5; }
            .top { position: relative; height: 118px; overflow: hidden; border-bottom: 1px solid #dbe7ff; background: linear-gradient(135deg, #eef6ff 0%, #fff 58%, #eef5ff 100%); }
            .top:before { content: ""; position: absolute; width: 290px; height: 90px; right: -70px; top: -58px; border: 12px solid #b9d5fb; border-radius: 0 0 0 180px; transform: rotate(-8deg); }
            .top:after { content: ""; position: absolute; width: 260px; height: 40px; right: -50px; top: 18px; border-top: 9px solid #3b82d0; border-radius: 50%; transform: rotate(-12deg); }
            .brand { position: absolute; z-index: 2; left: 22px; top: 20px; display: flex; align-items: center; gap: 12px; }
            .logo { width: 72px; height: 72px; border-radius: 12px; object-fit: contain; background: #111a2d; }
            .brand-text strong { display: block; color: #22569d; font-size: 27px; line-height: 1; }
            .brand-text span { display: block; margin-top: 4px; color: #314a6b; font-size: 9px; }
            .contact { position: absolute; z-index: 3; right: 22px; top: 26px; color: #315d96; font-size: 9px; line-height: 1.8; text-align: right; }
            .contact strong { color: #173d70; }
            .invoice-title { display: flex; height: 68px; align-items: center; justify-content: space-between; border-top: 1px solid #8ab0df; margin: 12px 22px 0; }
            .title-left { display: flex; height: 48px; align-items: center; gap: 10px; border-radius: 8px; padding: 0 14px; color: #fff; background: #214f94; }
            .title-icon { width: 27px; height: 31px; border: 2px solid #fff; border-radius: 4px; position: relative; }
            .title-icon:before { content: ""; position: absolute; left: 5px; right: 5px; top: 8px; height: 2px; background: #fff; box-shadow: 0 6px #fff, 0 12px #fff; }
            .title-left strong { display: block; font-size: 17px; letter-spacing: 1px; }
            .title-left span { display: block; margin-top: 3px; font-size: 8px; }
            .invoice-meta { text-align: right; color: #314a6b; font-size: 10px; line-height: 1.65; }
            .invoice-meta strong { color: #173d70; }
            .content { height: 180mm; display: flex; flex-direction: column; padding: 0 22px 16px; }
            .applicant-row { display: grid; grid-template-columns: minmax(0, 1fr) 160px; gap: 12px; margin-top: 8px; }
            .section { overflow: hidden; border: 1px solid #dde6f4; border-radius: 8px; }
            .section-title { display: flex; height: 36px; align-items: center; gap: 8px; padding: 0 12px; color: #fff; background: linear-gradient(90deg, #1e4f9a, #2e67ad); font-size: 12px; font-weight: 800; }
            .section-icon { width: 18px; height: 18px; border: 1px solid #fff; border-radius: 3px; position: relative; }
            .app-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 12px; padding: 7px 10px 8px; }
            .app-field { display: grid; grid-template-columns: 118px 1fr; min-height: 34px; align-items: center; border-bottom: 1px solid #edf1f7; }
            .app-field:last-child, .app-field:nth-last-child(2) { border-bottom: 0; }
            .app-field span { color: #35517a; font-size: 9px; }
            .app-field strong { color: #26344d; font-size: 10px; }
            .verification { display: grid; height: 100%; place-items: center; border: 1px solid #a8d6b5; border-radius: 8px; color: #1b9a55; background: #eaf8ef; text-align: center; }
            .score-row { display: flex; align-items: center; justify-content: center; gap: 7px; }
            .shield { width: 34px; height: 38px; border-radius: 4px 4px 12px 12px; color: #fff; background: #22a55a; clip-path: polygon(50% 0, 92% 16%, 82% 72%, 50% 100%, 18% 72%, 8% 16%); text-align: center; line-height: 38px; font-size: 16px; }
            .verification strong { color: #159447; font-size: 30px; line-height: 1; }
            .verification span { display: block; color: #2d5d47; font-size: 9px; }
            .progress { width: 110px; height: 5px; overflow: hidden; border-radius: 99px; background: #dbe8df; margin: 6px auto 4px; }
            .progress div { height: 100%; border-radius: 99px; background: #22a55a; }
            .status { display: inline-block; border-radius: 4px; padding: 4px 7px; color: #fff; background: #1f9e57; font-size: 8px; font-weight: 800; }
            .invoice-table { margin-top: 10px; border: 1px solid #bfcde0; border-radius: 8px; overflow: hidden; }
            table { width: 100%; border-collapse: collapse; }
            th { height: 36px; color: #314b70; background: #edf3fb; text-align: left; font-size: 10px; }
            th:last-child, td:last-child { width: 150px; text-align: right; }
            th:first-child, td:first-child { width: 35px; text-align: center; }
            th:nth-child(2), td:nth-child(2) { text-align: left; }
            td { height: 38px; border-bottom: 1px solid #edf1f7; color: #26344d; font-size: 10px; }
            tr:last-child td { border-bottom: 0; }
            .summary td { height: 24px; border: 0; background: #f7faff; font-weight: 800; }
            .summary .total-label, .summary .total-value { color: #2153a1; background: #eaf1ff; font-size: 11px; }
            .bottom-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
            .payment-grid { display: grid; grid-template-columns: 118px 1fr; gap: 0 8px; padding: 7px 10px; }
            .payment-grid span { color: #35517a; font-size: 9px; }
            .payment-grid strong { color: #26344d; font-size: 10px; }
            .terms { padding: 7px 10px 5px; }
            .terms ol { margin: 0; padding-left: 17px; color: #3e4c60; font-size: 8.5px; line-height: 1.65; }
            .footer { display: flex; height: 82px; align-items: flex-end; justify-content: space-between; margin-top: auto; border-bottom: 1px solid #2d63a7; }
            .qr { width: 55px; height: 55px; border: 5px solid #17213a; box-shadow: inset 0 0 0 3px #fff; background: repeating-linear-gradient(90deg,#17213a 0 4px,#fff 4px 8px,#17213a 8px 10px,#fff 10px 14px); }
            .thank-you { color: #314a6b; text-align: center; font-size: 9px; font-style: italic; }
            .thank-you strong { display: block; color: #1f568e; }
            .signature { width: 150px; color: #315d96; text-align: right; font-size: 7px; }
            .signature-line { height: 28px; border-bottom: 1px solid #8da0ba; font-size: 9px; }
            .agency-name { color: #26344d; font-size: 8px; font-weight: 800; }
            @media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
          </style>
        </head>
        <body>
          <main class="sheet">
            <header class="top">
              <div class="brand">
                <img class="logo" src="/global-eagle-logo.jpg" alt="GLOBAL EAGLE" />
                <div class="brand-text">
                  <strong>GLOBAL EAGLE</strong>
                  <span>Visa Application &amp; Document Processing Services</span>
                  <span><b>Your Trusted Visa Service Partner</b></span>
                </div>
              </div>
              <div class="contact">
                <strong>☎ &nbsp; +855 23 999 999</strong><br />
                <strong>✉ &nbsp; info@visadoc.ai</strong><br />
                <strong>⌖ &nbsp; Phnom Penh, Cambodia</strong>
              </div>
            </header>

            <div class="invoice-title">
              <div class="title-left">
                <span class="title-icon"></span>
                <div><strong>VISA INVOICE</strong><span>Visa Application Assessment Report</span></div>
              </div>
              <div class="invoice-meta">
                <div><strong>Invoice No:</strong> ${escapeHtml(invoiceReference)}</div>
                <div><strong>Issue Date:</strong> ${escapeHtml(issueDate)}</div>
                <div><strong>Due Date:</strong> ${escapeHtml(dueDate)}</div>
              </div>
            </div>

            <div class="content">
              <div class="applicant-row">
                <section class="section">
                  <div class="section-title"><span class="section-icon"></span> Applicant Information</div>
                  <div class="app-grid">
                    <div class="app-field"><span>Applicant Name</span><strong>: ${escapeHtml(applicant.fullName || "—")}</strong></div>
                    <div class="app-field"><span>Destination Country</span><strong>: ${escapeHtml(application.destination || "—")}</strong></div>
                    <div class="app-field"><span>Passport Number</span><strong>: ${escapeHtml(applicant.passportNumber || "—")}</strong></div>
                    <div class="app-field"><span>Visa Type</span><strong>: ${escapeHtml(application.visaTypeName || "—")}</strong></div>
                    <div class="app-field"><span>Nationality</span><strong>: ${escapeHtml(applicant.nationality || "—")}</strong></div>
                    <div class="app-field"><span>Application ID</span><strong>: ${escapeHtml(application.applicationNumber || application.id || "—")}</strong></div>
                  </div>
                </section>
                <section class="verification">
                  <div>
                    <div class="score-row"><span class="shield">✓</span><strong>${Math.round(verificationPercent)}%</strong></div>
                    <span>Document Verified</span>
                    <div class="progress"><div style="width:${Math.max(0, Math.min(100, verificationPercent))}%"></div></div>
                    <div class="status">✓ ${escapeHtml(approved ? "APPROVED FOR REVIEW" : verificationStatus.toUpperCase())}</div>
                  </div>
                </section>
              </div>

              <section class="invoice-table">
                <div class="section-title"><span class="section-icon"></span> Verification Details</div>
                <table>
                  <thead><tr><th>#</th><th>Document Type</th><th>Status</th><th>Score</th><th>Remarks</th></tr></thead>
                  <tbody>${verificationRows}</tbody>
                </table>
              </section>

              <div class="bottom-row">
                <section class="section">
                  <div class="section-title"><span class="section-icon"></span> Payment Information</div>
                  <div class="payment-grid">
                    <span>Payment Method</span><strong>: Not recorded</strong>
                    <span>Payment Status</span><strong>: ${escapeHtml(finalized ? "Paid" : "Unpaid")}</strong>
                    <span>Amount Paid</span><strong>: Not recorded</strong>
                    <span>Balance Due</span><strong>: Not recorded</strong>
                  </div>
                </section>
                <section class="section">
                  <div class="section-title"><span class="section-icon"></span> Terms &amp; Conditions</div>
                  <div class="terms">
                    <ol>
                      <li>Payment must be made by the due date stated above.</li>
                      <li>Government visa fees are subject to the applicable embassy or immigration authority.</li>
                      <li>Service fees are separate from government visa fees.</li>
                      <li>Visa approval is determined solely by the relevant immigration authority. Payment does not guarantee visa approval.</li>
                      <li>Refunds are subject to the applicable service agreement and refund policy.</li>
                    </ol>
                  </div>
                </section>
              </div>

              <footer class="footer">
                <div class="qr"></div>
                <div class="thank-you"><strong>Thank you for choosing GLOBAL EAGLE</strong><span>Visa Application &amp; Document Processing</span></div>
                <div class="signature">
                  <div class="signature-line"></div>
                  <div class="agency-name">Authorized Signature</div>
                  <div>GLOBAL EAGLE</div>
                </div>
              </footer>
            </div>
          </main>
        </body>
      </html>`);
    printDocument.close();
    const printWindow = printFrame.contentWindow;
    if (!printWindow) {
      printFrame.remove();
      setError("Unable to prepare the invoice for printing.");
      return;
    }
    window.setTimeout(() => {
      printWindow.focus();
      printWindow.print();
      window.setTimeout(() => printFrame.remove(), 1000);
    }, 250);
  };
  const pdfAction = printReport;
  const bankDocuments = documents.filter((item) => item.bankStatementAnalysis);
  const requirementScores = summary.requirementScores || [];
  const requirementById = Object.fromEntries(
    requirementScores.map((item) => [String(item.requirementId), item]),
  );
  const documentName = (item) =>
    requirementById[String(item.requirementId)]?.name ||
    item.requirementCode ||
    item.fileName ||
    "Document";
  const statusMeta = (status) => {
    const normalized = String(status || "PROCESSING").toUpperCase();
    if (normalized === "VERIFIED" || normalized === "PASS") {
      return {
        label: "Verified",
        icon: CheckCircle2,
        badge: "bg-emerald-50 text-emerald-700",
        border: "border-emerald-200",
      };
    }
    if (normalized === "REJECTED" || normalized === "FAIL") {
      return {
        label: "Rejected",
        icon: CircleX,
        badge: "bg-red-50 text-red-700",
        border: "border-red-200",
      };
    }
    if (normalized === "NOT_UPLOADED" || normalized === "MISSING") {
      return {
        label: "Not submitted",
        icon: FileText,
        badge: "bg-slate-100 text-slate-600",
        border: "border-slate-200",
      };
    }
    return {
      label: normalized === "NEEDS_REVIEW" ? "Pending review" : "Processing",
      icon: Hourglass,
      badge: "bg-amber-50 text-amber-700",
      border: "border-amber-200",
    };
  };
  const verificationSummaryMetrics = [
    {
      label: "Required documents verified",
      value: `${summary.verificationPercentage ?? 0}%`,
    },
    {
      label: "Document readiness",
      value: `${summary.documentReadiness ?? 0}%`,
    },
    {
      label: "Earned document points",
      value: `${summary.earnedPoints ?? 0} / ${summary.maximumPoints ?? 0}`,
    },
    {
      label: "OCR page progress",
      value: `${summary.ocrProgress?.percentage ?? 0}%`,
      detail: `${processedPages} of ${totalPages} pages processed`,
    },
    {
      label: "Missing required documents",
      value: summary.missingRequiredDocuments?.length ?? 0,
    },
  ];
  return (
    <main className="verification-report mx-auto max-w-[1500px] space-y-5">
      <header className="verification-hero flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <p className="verification-breadcrumb text-xs font-semibold text-blue-700">
              Visa Applications <span aria-hidden="true">›</span> Verification Results
            </p>
            <h1 className="text-2xl font-bold text-slate-900">
              Application assessment
            </h1>
            <p className="text-sm text-slate-600">
              {applicant.fullName || "Applicant"} ·{" "}
              {application.visaTypeName ||
                application.destination ||
                "Visa type unavailable"}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/applications"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to
            applications
          </Link>
          <button
            type="button"
            onClick={pdfAction}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-700 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-800"
          >
            <Download className="h-4 w-4" aria-hidden="true" />{" "}
            Print / Save PDF
          </button>
          <button
            type="button"
            onClick={printReport}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            <Printer className="h-4 w-4" aria-hidden="true" /> Print
          </button>
        </div>
      </header>

      {error && (
        <p
          role="alert"
          className="rounded-xl bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          {notice}
        </p>
      )}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          {
            label: "Total documents",
            value: summary.totalDocuments ?? documents.length,
            detail: `${summary.totalDocuments ?? documents.length} documents uploaded`,
            Icon: FolderOpen,
            color: "bg-blue-50 text-blue-700",
          },
          {
            label: "Verified documents",
            value: summary.verifiedDocuments ?? 0,
            detail: `${summary.totalDocuments ? Math.round(((summary.verifiedDocuments || 0) / summary.totalDocuments) * 100) : 0}% complete`,
            Icon: CheckCircle2,
            color: "bg-emerald-50 text-emerald-600",
          },
          {
            label: "Pending review",
            value: pendingDocuments,
            detail: `${pendingDocuments ? "Needs attention" : "0%"}`,
            Icon: Hourglass,
            color: "bg-amber-50 text-amber-600",
          },
          {
            label: "Rejected documents",
            value: summary.rejectedDocuments ?? 0,
            detail: "0%",
            Icon: CircleX,
            color: "bg-red-50 text-red-600",
          },
          {
            label: "Submission completeness",
            value: `${summary.submissionCompleteness ?? 0}%`,
            detail: `${summary.submittedRequiredDocuments ?? 0}/${summary.totalRequiredDocuments ?? 0} required submitted`,
            Icon: Percent,
            color: "bg-violet-50 text-violet-600",
          },
        ].map(({ label, value, detail, Icon, color }) => (
          <article
            key={label}
            className="verification-metric rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-slate-500">{label}</p>
                <p className="mt-2 text-2xl font-extrabold text-slate-900">{value}</p>
                <p className="mt-1 text-[11px] font-medium text-slate-500">{detail}</p>
              </div>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${label === "Rejected documents" ? "bg-red-500" : label === "Pending review" ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{
                  width: `${label === "Submission completeness" ? summary.submissionCompleteness || 0 : label === "Verified documents" ? (summary.totalDocuments ? ((summary.verifiedDocuments || 0) / summary.totalDocuments) * 100 : 0) : label === "Total documents" ? 100 : label === "Rejected documents" ? (summary.totalDocuments ? ((summary.rejectedDocuments || 0) / summary.totalDocuments) * 100 : 0) : (pendingDocuments ? 45 : 0)}%`,
                }}
              />
            </div>
          </article>
        ))}
      </section>

      <section>
        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <FolderOpen className="h-5 w-5 text-blue-700" /> Document list
              </h2>
              <p className="mt-1 text-xs text-slate-500">Uploaded files and their current verification state</p>
            </div>
          </div>
          <div className="divide-y divide-slate-100">
            {documents.length ? documents.map((item) => {
              const meta = statusMeta(item.status);
              const StatusIcon = meta.icon;
              const selected = selectedDocument?.documentId === item.documentId;
              return (
                <button
                  type="button"
                  key={item.documentId}
                  aria-pressed={selected}
                  onClick={() => {
                    setSelectedDocumentId(item.documentId);
                    setIsDocumentPreviewOpen(true);
                  }}
                  className={`verification-document-row flex w-full items-center gap-3 p-4 text-left transition ${selected ? "bg-blue-50/50" : "hover:bg-blue-50/50"} ${meta.label === "Verified" ? "is-verified" : meta.label === "Rejected" ? "is-rejected" : "is-pending"}`}
                >
                  {documentThumbnails[item.documentId] ? (
                    <img src={documentThumbnails[item.documentId]} alt="" className="h-14 w-16 rounded-lg border border-slate-200 object-cover" />
                  ) : (
                    <span className="flex h-14 w-16 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700"><FileText className="h-7 w-7" /></span>
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold text-slate-900">{documentName(item)}</span>
                    <span className="mt-1 block truncate text-xs text-slate-500">{item.fileName}</span>
                    {item.uploadDate && <span className="mt-1 block text-[11px] text-slate-400">Uploaded {new Date(item.uploadDate).toLocaleDateString()}</span>}
                  </span>
                  <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${meta.badge}`}>
                    <StatusIcon className="h-3.5 w-3.5" /> {meta.label}
                  </span>
                </button>
              );
            }) : (
              <p className="p-6 text-sm text-slate-500">No documents uploaded.</p>
            )}
          </div>
          {selectedDocument && (
            <div className="border-t border-slate-100">
              <button
                type="button"
                aria-expanded={isDocumentPreviewOpen}
                aria-controls="document-preview-content"
                onClick={() => setIsDocumentPreviewOpen((open) => !open)}
                className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-bold text-slate-900 hover:bg-slate-50"
              >
                <span className="flex items-center gap-2">
                  <Eye className="h-4 w-4 text-blue-700" />
                  Document preview
                </span>
                <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${isDocumentPreviewOpen ? "rotate-180" : ""}`} />
              </button>
              <div
                id="document-preview-content"
                hidden={!isDocumentPreviewOpen}
                aria-hidden={!isDocumentPreviewOpen}
                style={{ display: isDocumentPreviewOpen ? "flex" : "none" }}
                className="flex min-h-[300px] items-center justify-center bg-slate-50 p-3"
              >
                {selectedDocument.mimeType?.startsWith("image/") ? (
                  documentThumbnails[selectedDocument.documentId] ? (
                    <img
                      src={documentThumbnails[selectedDocument.documentId]}
                      alt={selectedDocument.fileName}
                      className="max-h-[420px] max-w-full object-contain"
                    />
                  ) : Object.prototype.hasOwnProperty.call(documentThumbnails, selectedDocument.documentId) ? (
                    <p className="text-sm text-slate-500">Image preview could not be loaded.</p>
                  ) : (
                    <p className="text-sm text-slate-500">Loading document…</p>
                  )
                ) : selectedDocument.mimeType === "application/pdf" ? (
                  documentPdfPreview.documentId === selectedDocument.documentId && documentPdfPreview.url ? (
                    <iframe
                      title={selectedDocument.fileName}
                      src={documentPdfPreview.url}
                      className="h-[420px] w-full rounded-lg bg-white"
                    />
                  ) : (
                    <p className="text-sm text-slate-500">
                      {documentPdfPreview.documentId === selectedDocument.documentId && documentPdfPreview.error
                        ? documentPdfPreview.error
                        : "Loading document…"}
                    </p>
                  )
                ) : (
                  <p className="text-sm text-slate-500">
                    Preview is not available for this file type.
                  </p>
                )}
              </div>
            </div>
          )}
        </article>

      </section>

      <section className="hidden grid gap-4 xl:grid-cols-[1.65fr_1fr]">
        <article className="grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:grid-cols-2">
          <div>
            <h2 className="mb-3 text-sm font-bold text-slate-900">
              Applicant information
            </h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="text-slate-500">Name</dt>
              <dd className="font-medium text-slate-900">
                {applicant.fullName || "—"}
              </dd>
              <dt className="text-slate-500">Nationality</dt>
              <dd className="font-medium text-slate-900">
                {applicant.nationality || "—"}
              </dd>
              <dt className="text-slate-500">Passport</dt>
              <dd className="font-medium text-slate-900">
                {applicant.passportNumber || "—"}
              </dd>
              <dt className="text-slate-500">Application ID</dt>
              <dd className="break-all font-medium text-slate-900">
                {application.applicationNumber || application.id || "—"}
              </dd>
              <dt className="text-slate-500">Destination</dt>
              <dd className="font-medium text-slate-900">
                {application.destination || "—"}
              </dd>
              <dt className="text-slate-500">Visa type</dt>
              <dd className="font-medium text-slate-900">
                {application.visaTypeName || "—"}
              </dd>
            </dl>
          </div>
          <div className="border-t border-slate-100 pt-4 md:border-l md:border-t-0 md:pl-5 md:pt-0">
            <h2 className="mb-3 text-sm font-bold text-slate-900">
              Report details
            </h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="flex items-center gap-1 text-slate-500">
                <CalendarDays className="h-4 w-4" />
                Date
              </dt>
              <dd className="font-medium text-slate-900">
                {reportDate ? new Date(reportDate).toLocaleString() : "—"}
              </dd>
              <dt className="text-slate-500">Report ID</dt>
              <dd className="break-all font-medium text-slate-900">
                {latestVerification?._id || "NOT AVAILABLE"}
              </dd>
              <dt className="text-slate-500">Reviewed by</dt>
              <dd className="font-medium text-slate-900">
                {report.verifiedBy || "Not finalized"}
              </dd>
              <dt className="text-slate-500">Receipt</dt>
              <dd className="font-medium text-slate-900">
                {receiptNumber || "Not issued"}
              </dd>
            </dl>
          </div>
        </article>
        <article
          className={`flex min-h-40 flex-col items-center justify-center rounded-2xl border p-5 text-center shadow-sm ${statusStyle}`}
        >
          {reportStatus === "VERIFIED" ? (
            <CircleCheck className="h-12 w-12" aria-hidden="true" />
          ) : reportStatus === "REJECTED" ? (
            <CircleX className="h-12 w-12" aria-hidden="true" />
          ) : (
            <Clock3 className="h-12 w-12" aria-hidden="true" />
          )}
          <h2 className="mt-2 text-lg font-bold">Verification status</h2>
          <p className="text-2xl font-extrabold">{reportStatus}</p>
          <p className="mt-1 text-xs">
            {finalized
              ? "Staff-finalized document review"
              : "Automated checks only · authenticity not confirmed"}
          </p>
        </article>
      </section>

      <section className="hidden grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          label="Total documents"
          value={summary.totalDocuments ?? documents.length}
        />
        <Metric
          label="Verified documents"
          value={summary.verifiedDocuments ?? 0}
        />
        <Metric label="Pending review" value={pendingDocuments} />
        <Metric
          label="Rejected documents"
          value={summary.rejectedDocuments ?? 0}
        />
        <Metric
          label="Submitted required documents"
          value={`${summary.submissionCompleteness ?? 0}%`}
        />
      </section>

      <section className="hidden rounded-2xl border border-blue-200 bg-blue-50 p-5 shadow-sm">
        <h2 className="font-bold text-blue-950">Document-based assessment score</h2>
        <p className="mt-2 text-3xl font-extrabold text-blue-900">
          {summary.documentAssessmentScore ?? summary.documentReadiness ?? 0}%
        </p>
        <p className="mt-1 text-sm text-blue-900">
          Earned {summary.earnedPoints ?? summary.verifiedPoints ?? 0} / {summary.maximumPoints ?? summary.totalAvailablePoints ?? 0} configured points
        </p>
        <p className="mt-1 text-xs text-blue-800">
          Required completed: {summary.verifiedRequiredDocuments ?? 0} / {summary.totalRequiredDocuments ?? 0} · Missing: {summary.missingRequiredDocuments?.length ?? 0} · Processing: {summary.processingDocuments ?? 0} · Rejected: {summary.rejectedDocuments ?? 0} · Needs review: {summary.needsReviewDocuments ?? 0}
        </p>
        <p className="mt-1 text-xs text-blue-800">
          Last calculated: {summary.lastCalculationAt ? new Date(summary.lastCalculationAt).toLocaleString() : "Not calculated"}
        </p>
      </section>

      <p className="hidden flex items-start gap-2 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        OCR and automated consistency checks do not prove document authenticity
        or determine visa approval. Only a recorded staff decision is shown as
        finalized.
      </p>

      <section className="grid gap-4">
        <article className="verification-panel overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 bg-white px-4 py-3 text-sm font-bold text-slate-900">
            <span className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-blue-700" />
            Automated verification results
            </span>
            <span className="text-xs font-semibold text-blue-700">View detailed report →</span>
          </div>
          <div className="grid divide-y divide-slate-200 md:grid-cols-3 md:divide-x md:divide-y-0">
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">OCR results</h3>
              <p className="mt-2 text-sm font-semibold text-slate-800">
                {summary.ocrCompletedDocuments ?? 0}/{summary.totalDocuments ?? documents.length} documents
              </p>
              <p className="mt-1 text-xs text-slate-600">{processedPages}/{totalPages} pages · {summary.documentProcessingStatus || "Status unavailable"}</p>
            </div>
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">Field checks</h3>
              <p className="mt-2 text-sm font-semibold text-slate-800">{passedChecks.length}/{allChecks.length} passed</p>
              <p className="mt-1 text-xs text-slate-600">{summary.automatedVerificationStatus || "Status unavailable"}</p>
            </div>
            <div className="p-4">
              <h3 className="text-sm font-bold text-slate-900">Consistency</h3>
              {bankDocuments.length ? (
                bankDocuments.map((item) => (
                  <div key={item.documentId} className="mt-2 text-xs text-slate-600">
                    <p className="truncate">{item.fileName}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <span className="rounded-full bg-slate-100 px-2 py-1">{item.abaStatementAnalysis?.reviewStatus || item.bankStatementAnalysis.financialRequirementStatus || "Status unavailable"}</span>
                      <span className="rounded-full bg-slate-100 px-2 py-1">{item.abaStatementAnalysis?.reconciliationStatus || item.bankStatementAnalysis.reconciliation?.status || "Reconciliation unavailable"}</span>
                    </div>
                  </div>
                ))
              ) : (
                <p className="mt-3 text-xs text-slate-500">
                  No bank statement consistency result available.
                </p>
              )}
            </div>
          </div>
          {unresolvedChecks.length > 0 && (
            <details className="border-t border-amber-200 bg-amber-50 p-4">
              <summary className="cursor-pointer text-sm font-bold text-amber-900">
                {unresolvedChecks.length} check{unresolvedChecks.length === 1 ? "" : "s"} need review
              </summary>
              <ul className="mt-3 grid gap-2 text-xs text-amber-900 sm:grid-cols-2">
                {unresolvedChecks.map((check, index) => (
                  <li key={`${check.fileName}-${check.code}-${index}`}>
                    {check.fileName}: {check.label} · {check.status}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </article>
      </section>

      <section className="grid gap-4">
        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-700">
              <CircleCheck className="h-5 w-5" />
            </span>
            <div>
              <h2 className="font-bold text-slate-900">Verification summary</h2>
              <p className="mt-1 text-sm text-slate-500">
                Document workflow progress at a glance
              </p>
            </div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {verificationSummaryMetrics.map((item) => (
              <div
                key={item.label}
                className="rounded-xl border border-slate-100 bg-slate-50/80 p-4"
              >
                <p className="text-xs font-medium text-slate-500">{item.label}</p>
                <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">
                  {item.value}
                </p>
                {item.detail && (
                  <p className="mt-1 text-xs text-slate-500">{item.detail}</p>
                )}
              </div>
            ))}
          </div>
          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
            Workflow indicators only; they do not represent visa approval probability.
          </p>
        </article>
        {readiness && summary.scoringVersion !== "evidence-v3" && (
          <article className="rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-indigo-700">Document Requirement Score</p>
                <p className="mt-1 text-xs text-slate-500">Calculated from the application's saved Requirements Management configuration</p>
              </div>
              <div className="text-right">
                <p className="text-4xl font-extrabold text-slate-900">{readiness.score}%</p>
                <p className="text-xs font-semibold text-slate-500">{readiness.earnedPoints} / {readiness.maximum} points</p>
              </div>
            </div>
            <div className="mt-3 h-3 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-indigo-600" style={{ width: `${Math.max(0, Math.min(100, readiness.score || 0))}%` }} />
            </div>
            {readiness.manualReviewRequired && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <strong>Manual review required:</strong> One or more documents are processing, incomplete, or awaiting review.
                {readiness.reviewFlags?.length > 0 && <p className="mt-1">{readiness.reviewFlags.join(" · ")}</p>}
              </div>
            )}
            {readiness.gateApplied && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <strong>Required-document gate:</strong> Required items remain incomplete.
                {readiness.missingRequiredDocuments?.length > 0 && <p className="mt-1">Missing: {readiness.missingRequiredDocuments.join(", ")}</p>}
              </div>
            )}
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Object.entries(readiness.components || {}).map(([key, item]) => (
                <div key={key} className="min-h-20 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
                  <p className="text-xs font-medium capitalize text-slate-500">
                    {item.label || key.replaceAll("_", " ")}
                  </p>
                  <p className="mt-1 text-lg font-bold text-slate-900">
                    {item.points == null ? "Not available" : item.points} / {item.maximum}
                  </p>
                  {key === "bank_statement" && (
                    <>
                      <p className="mt-2 text-xs text-slate-500">
                        OCR confidence: {item.ocrConfidence == null ? "Unavailable" : `${Math.round(item.ocrConfidence * 100)}%`}
                        {" · "}Balance reconciliation: {item.balanceReconciliationStatus || "NOT_AVAILABLE"}
                      </p>
                      {Object.keys(item.fieldConfidence || {}).length > 0 && (
                        <details className="mt-2 text-xs text-slate-600">
                          <summary className="cursor-pointer font-semibold">
                            OCR field confidence
                          </summary>
                          <ul className="mt-2 space-y-1 text-slate-500">
                            {Object.entries(item.fieldConfidence).map(([field, evidence]) => (
                              <li key={field}>
                                {field}: {evidence.confidence == null ? "confidence unavailable" : `${Math.round(evidence.confidence * 100)}%`}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
            {readiness.recommendations?.length > 0 && (
              <div className="mt-4">
                <h3 className="text-sm font-semibold text-slate-800">Recommended actions</h3>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
                  {readiness.recommendations.map((tip) => <li key={tip}>{tip}</li>)}
                </ul>
              </div>
            )}
            <p className="mt-4 text-xs text-slate-500">{readiness.disclaimer}</p>
          </article>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center gap-2 bg-slate-900 px-4 py-3 text-sm font-bold text-white">
          <FileText className="h-4 w-4" />
          Document verification details
        </div>
        {documents.length ? (
          <div className="divide-y divide-slate-100">
            {documents.map((item) => {
              const bankAssessment =
                bankRequirementScores[item.documentId] ||
                item.bankStatementAnalysis;
              const bankAnalysis = item.bankStatementAnalysis || {};
              const monthlyBalanceChecks =
                bankAssessment?.monthlyBalanceChecks ||
                bankAnalysis.monthlyBalanceChecks ||
                [];
              const computedBankScore = bankAssessment?.computedBankScore;
              const confirmedBankScore = bankAssessment?.confirmedBankScore;
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
              const requirementContribution =
                bankAssessment?.requirementContribution;
              const isBankStatement =
                Boolean(bankAssessment) ||
                /bank.*statement|bank_statement/i.test(
                  `${item.requirementCode || ""} ${item.requirementName || ""}`,
                );
              return (
              <details key={item.documentId} className="group p-4">
                <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 flex-1 truncate font-semibold text-slate-900">{item.fileName}</span>
                  <span className="text-xs text-slate-600">{item.status}</span>
                  {isBankStatement && <span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-semibold text-teal-800">
                    {computedBankScore?.available
                      ? `Current calculated score: ${Number(computedBankScore.points).toFixed(2)} / ${computedBankScore.maximum}${bankScorePercentage == null ? "" : ` · ${bankScorePercentage}%`}`
                      : "Score unavailable"}
                  </span>}
                </summary>
                <div className="mt-4 space-y-4">
                  {!isBankStatement && <p className="text-sm font-semibold text-slate-800">
                    Score: {item.awardedPoints ?? "Not available"} / {item.assignedPoints ?? "Not available"}
                  </p>}
                  {isBankStatement && (
                    <details className="rounded-lg border border-slate-200 px-3 py-1 text-xs text-slate-600">
                      <summary className="cursor-pointer font-semibold">Score and review details</summary>
                      <div className="grid gap-2 py-2 sm:grid-cols-2">
                        <p>Document contribution: {requirementContribution?.available && requirementContribution.points != null
                          ? `${Number(requirementContribution.points).toFixed(2)} / ${requirementContribution.maximum}`
                          : `Unavailable / ${requirementContribution?.maximum ?? "—"}`}</p>
                        <p>Confirmed score: {confirmedBankScore?.status === "CONFIRMED"
                          ? `${confirmedBankScore.points} / ${confirmedBankScore.maximum}`
                          : "Not yet confirmed"}</p>
                      </div>
                    </details>
                  )}
                  {isBankStatement && monthlyBalanceChecks.length > 0 && (
                      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs">
                        <p className="font-semibold text-slate-700">Monthly closing balances <span className="font-normal text-slate-500">· supporting evidence</span></p>
                        <ul className="mt-2 grid gap-2 text-slate-600 sm:grid-cols-3">
                          {monthlyBalanceChecks.map((month) => {
                            const formatUsd = (value) => {
                              const amount = Number(value);
                              return value == null || !Number.isFinite(amount)
                                ? "Unavailable"
                                : `USD ${amount.toLocaleString("en-US", {
                                    minimumFractionDigits: 2,
                                    maximumFractionDigits: 2,
                                  })}`;
                            };
                            const belowMinimum =
                              month.minimumBalanceStatus === "BELOW_MINIMUM";
                            return (
                              <li key={month.month} className={`rounded-md p-2 ${belowMinimum ? "border border-red-200 bg-red-50" : "bg-slate-50"}`}>
                                <strong className="text-slate-800">{month.month}</strong>
                                {" · "}
                                {belowMinimum ? (
                                  <span className="font-semibold text-red-700">Below minimum · manual review</span>
                                ) : month.closingBalance == null ? (
                                  "Balance unavailable"
                                ) : (
                                  `${String(month.currency || "USD").toUpperCase()} ${Number(month.closingBalance).toLocaleString(
                                    "en-US",
                                    { minimumFractionDigits: 2, maximumFractionDigits: 2 },
                                  )}`
                                )}
                                {belowMinimum && (
                                  <span className="mt-1 block font-medium text-red-700">
                                    Closing balance: {formatUsd(month.closingBalance)} · Minimum: {formatUsd(month.minimumRequired)} · Shortfall: {formatUsd(month.shortfall)}
                                  </span>
                                )}
                                {month.sourcePage
                                  ? <span className="mt-1 block text-slate-500">Page {month.sourcePage}{month.sourceTransaction ? ` · row ${month.sourceTransaction}` : ""}</span>
                                  : ""}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
                  <details className="text-xs text-slate-600">
                    <summary className="cursor-pointer font-semibold">Document and OCR details</summary>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <p>Reference: {item.documentReference || "Unavailable"}</p>
                      <p>OCR: {item.ocrStatus || "Unavailable"} · {item.pagesProcessed}/{item.totalPages} pages</p>
                      <p>OCR confidence: {item.ocrConfidence == null ? "Unavailable" : `${Math.round(Number(item.ocrConfidence) * 100)}%`}</p>
                      <p>Missing fields: {(item.missingFields || []).length ? item.missingFields.join(", ") : "None"}</p>
                      {summary.scoringVersion !== "evidence-v3" && <p>Points: {item.awardedPoints}/{item.assignedPoints}</p>}
                    </div>
                  </details>
                  {item.automatedVerification?.crossDocumentChecks?.some(
                    (check) => check.status !== "PASSED",
                  ) && (
                    <details className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                      <summary className="cursor-pointer font-semibold">
                        Other review notes ({item.automatedVerification.crossDocumentChecks.filter((check) => check.status !== "PASSED").length})
                      </summary>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {item.automatedVerification.crossDocumentChecks
                          .filter((check) => check.status !== "PASSED")
                          .map((check) => <li key={check.code}>{check.label}: {check.reason}</li>)}
                      </ul>
                    </details>
                  )}
                  {item.automatedVerification && !item.abaStatementAnalysis && (
                    <AutomatedVerificationReport
                      verification={item.automatedVerification}
                      isPhoto={isPhotoDocument(item)}
                    />
                  )}
                  {item.abaStatementAnalysis && canManage && (
                    <AbaBankPanel documentId={item.documentId} applicationId={applicationId} canManage={canManage} onChange={loadResults} />
                  )}
                  {item.bankStatementAnalysis && !item.abaStatementAnalysis && (
                      <BankStatementAnalysis
                        analysis={item.bankStatementAnalysis}
                        documentId={item.documentId}
                        canManage={canManage}
                        onChange={loadResults}
                        requirementScore={{
                          ...(bankRequirementScores[item.documentId] || {}),
                          maximumConfiguredPoints:
                            bankRequirementScores[item.documentId]?.bankDiagnosticScore
                              ? (summary.requirementScores || []).find(
                                  (requirement) =>
                                    String(requirement.requirementId || requirement._id) === String(item.requirementId),
                                )?.configuredPoints || item.assignedPoints || 0
                              : item.assignedPoints || 0,
                        }}
                      />
                  )}
                  {(item.verificationComments || item.rejectionReason) && (
                    <p className="text-sm text-slate-600">
                      {item.verificationComments || item.rejectionReason}
                    </p>
                  )}
                  {canManage &&
                    !finalized &&
                    !item.abaStatementAnalysis &&
                    (isPhotoDocument(item) ||
                      item.ocrStatus === "OCR_COMPLETED") && (
                    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-amber-900">
                          Staff review / override
                        </p>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            rerunDocumentVerification(item.documentId)
                          }
                          className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs font-semibold text-blue-800 disabled:opacity-50"
                        >
                          Rerun automated checks
                        </button>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-[180px_1fr_auto]">
                        <select
                          aria-label={`Review status for ${item.fileName}`}
                          value={
                            reviewForms[item.documentId]?.status ||
                            "NEEDS_REVIEW"
                          }
                          onChange={(event) =>
                            setReviewForms((current) => ({
                              ...current,
                              [item.documentId]: {
                                ...current[item.documentId],
                                status: event.target.value,
                              },
                            }))
                          }
                          className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm"
                        >
                          <option value="NEEDS_REVIEW">Needs review</option>
                          <option value="VERIFIED">Verified</option>
                          <option value="REJECTED">Rejected</option>
                          <option value="INSUFFICIENT_DATA">
                            Insufficient data
                          </option>
                        </select>
                        <input
                          aria-label={`Review reason for ${item.fileName}`}
                          value={reviewForms[item.documentId]?.reason || ""}
                          onChange={(event) =>
                            setReviewForms((current) => ({
                              ...current,
                              [item.documentId]: {
                                ...current[item.documentId],
                                reason: event.target.value,
                              },
                            }))
                          }
                          placeholder="Required reason for this review or override"
                          className="min-w-0 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm"
                        />
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => reviewDocument(item)}
                          className="rounded-lg bg-amber-800 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                        >
                          Save review
                        </button>
                      </div>
                      {item.manualReviewHistory?.length > 0 && (
                        <ul className="space-y-1 text-xs text-amber-950">
                          {item.manualReviewHistory.map((review, index) => (
                            <li key={`${review.reviewedAt}-${index}`}>
                              {review.status} · {review.reason} ·{" "}
                              {review.reviewedAt
                                ? new Date(review.reviewedAt).toLocaleString()
                                : "time unavailable"}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {Object.keys(item.extractedFields || {}).length > 0 && (
                    <details className="rounded-lg bg-slate-50 p-3">
                      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                        Extracted fields
                      </summary>
                      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                        {Object.entries(item.extractedFields).map(
                          ([key, value]) => (
                            <div key={key} className="min-w-0">
                              <dt className="font-medium text-slate-500">
                                {key}
                              </dt>
                              <dd className="break-words text-slate-800">
                                {Array.isArray(value)
                                  ? `${value.length} entries`
                                  : value && typeof value === "object"
                                    ? JSON.stringify(value)
                                    : String(value ?? "—")}
                              </dd>
                            </div>
                          ),
                        )}
                      </dl>
                    </details>
                  )}
                </div>
              </details>
              );
            })}
          </div>
        ) : (
          <p className="p-5 text-sm text-slate-500">No documents uploaded.</p>
        )}
      </section>

      <section className="verification-invoice" aria-label="Visa service invoice">
        <header className="verification-invoice-header">
          <div className="verification-invoice-brand">
            <span>V</span>
            <div>
              <strong>VISA DOC AI</strong>
              <small>Visa Application &amp; Document Processing Services</small>
              <small>Phnom Penh, Cambodia</small>
            </div>
          </div>
          <div className="verification-invoice-number">
            <span>Invoice</span>
            <strong>{receiptNumber || application.applicationNumber || `INV-${applicationId.slice(-8).toUpperCase()}`}</strong>
          </div>
        </header>

        <div className="verification-invoice-meta">
          <div>
            <span>Issue date</span>
            <strong>{reportDate ? new Date(reportDate).toLocaleDateString("en-US") : "—"}</strong>
          </div>
          <div>
            <span>Payment status</span>
            <strong>{finalized ? "Paid / recorded" : "Unpaid"}</strong>
          </div>
        </div>

        <div className="verification-invoice-section">
          <h2>Applicant information</h2>
          <div className="verification-invoice-grid">
            <div><span>Applicant name</span><strong>{applicant.fullName || "—"}</strong></div>
            <div><span>Passport number</span><strong>{applicant.passportNumber || "—"}</strong></div>
            <div><span>Nationality</span><strong>{applicant.nationality || "—"}</strong></div>
            <div><span>Destination country</span><strong>{application.destination || "—"}</strong></div>
            <div><span>Visa type</span><strong>{application.visaTypeName || "—"}</strong></div>
            <div><span>Application reference</span><strong>{application.applicationNumber || application.id || "—"}</strong></div>
          </div>
        </div>

        <div className="verification-invoice-section">
          <h2>Invoice details</h2>
          <table>
            <thead>
              <tr><th>Description</th><th>Amount (USD)</th></tr>
            </thead>
            <tbody>
              <tr><td>Visa Application Service Fee</td><td>Not recorded</td></tr>
              <tr><td>Document Preparation &amp; Review</td><td>Not recorded</td></tr>
              <tr><td>Visa Application Government Fee</td><td>Not recorded</td></tr>
              <tr><td>Additional Service Fee</td><td>Not recorded</td></tr>
              <tr><td>Discount</td><td>Not recorded</td></tr>
              <tr className="verification-invoice-total"><td>Total amount due</td><td>Not recorded</td></tr>
            </tbody>
          </table>
        </div>

        <div className="verification-invoice-section verification-invoice-payment">
          <h2>Payment information</h2>
          <div>
            <span>Payment method</span>
            <strong>Not recorded</strong>
          </div>
          <div>
            <span>Amount paid</span>
            <strong>Not recorded</strong>
          </div>
          <div>
            <span>Outstanding balance</span>
            <strong>Not recorded</strong>
          </div>
        </div>

        <footer className="verification-invoice-footer">
          <div>
            <h2>Terms &amp; conditions</h2>
            <ol>
              <li>Payment must be made by the due date stated above.</li>
              <li>Government visa fees are subject to the applicable authority.</li>
              <li>Service fees are separate from government visa fees.</li>
              <li>Visa approval is determined solely by the relevant immigration authority.</li>
            </ol>
          </div>
          <div className="verification-invoice-signature">
            <span>Authorized by</span>
            <strong>________________________</strong>
            <small>Signature</small>
          </div>
        </footer>
      </section>

    </main>
  );
}
