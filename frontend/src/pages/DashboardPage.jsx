import {
  BriefcaseBusiness,
  ClipboardCheck,
  FileText,
  Plane,
  Plus,
  RotateCcw,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, formatNumber, useSecureData } from "./pageUtils";
import { useEffect, useMemo, useRef, useState } from "react";

const emptyApplications = [];

function ReadinessBar({ value }) {
  const score = Math.max(0, Math.min(100, Number(value) || 0));
  const color =
    score < 50 ? "bg-amber-700" : score < 80 ? "bg-blue-600" : "bg-emerald-600";

  return (
    <div className="flex min-w-32 items-center gap-2">
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score}
      >
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs font-semibold text-slate-600">
        {formatNumber(score)}%
      </span>
    </div>
  );
}

function getVisaTypeLabel(application) {
  const visaType =
    application.visaTypeName ||
    application.visaCategory ||
    application.visaType;
  if (typeof visaType === "string") return visaType;
  if (visaType && typeof visaType === "object") {
    return visaType.name || visaType.visaTypeCode || "";
  }
  return "";
}

function getVisaTypeCode(application) {
  if (typeof application.visaTypeCode === "string") {
    return application.visaTypeCode;
  }
  if (application.visaType && typeof application.visaType === "object") {
    return application.visaType.visaTypeCode || "";
  }
  return "";
}

export function DashboardPage({ profile = {} }) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [visaFilter, setVisaFilter] = useState("all");
  const [refreshedDashboardData, setRefreshedDashboardData] = useState(null);
  const [refreshError, setRefreshError] = useState("");
  const refreshingDashboard = useRef(false);
  const {
    data: dashboardData,
    loading,
    error,
  } = useSecureData(
    async () => {
      const response = await api.get("/dashboard");
      return response.data;
    },
    {
      metrics: {},
      analytics: {},
      recentActivities: [],
      recentApplications: [],
    },
  );

  useEffect(() => {
    const active = { current: true };
    const refreshDashboard = async () => {
      if (refreshingDashboard.current) return;
      refreshingDashboard.current = true;
      try {
        const response = await api.get("/dashboard");
        if (active.current) {
          setRefreshedDashboardData(response.data);
          setRefreshError("");
        }
      } catch (requestError) {
        if (active.current) {
          setRefreshError(
            requestError?.response?.data?.message ||
              "Unable to refresh dashboard data.",
          );
        }
      } finally {
        refreshingDashboard.current = false;
      }
    };
    const timer = window.setInterval(() => {
      void refreshDashboard();
    }, 5000);
    return () => {
    active.current = false;
      window.clearInterval(timer);
    };
  }, []);

  const currentDashboardData = refreshedDashboardData || dashboardData;
  const dashboardError = error || refreshError;
  const metrics = currentDashboardData.metrics || {};
  const analytics = currentDashboardData.analytics || {};
  const applications =
    currentDashboardData.recentApplications ?? emptyApplications;
  const visaTypes = analytics.visaTypeDistribution || [];
  const applicationTrend = analytics.applicationTrend || [];
  const visaStatusDistribution = [
    {
      name: t("approvedApplications"),
      value: metrics.approvedApplications || 0,
      color: "#059669",
    },
    {
      name: t("pending"),
      value: metrics.pendingApplications || 0,
      color: "#2563eb",
    },
    {
      name: t("rejected"),
      value: metrics.rejectedApplications || 0,
      color: "#e11d48",
    },
  ].filter((item) => item.value > 0);
  const visaStatusTotal = visaStatusDistribution.reduce(
    (sum, item) => sum + item.value,
    0,
  );
  const filteredApplications = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return applications.filter((application) => {
      const matchesQuery =
        !normalizedQuery ||
        [
          application.applicant,
          application.applicationNumber,
          getVisaTypeCode(application),
          getVisaTypeLabel(application),
        ]
          .filter(Boolean)
          .some((value) =>
            String(value).toLowerCase().includes(normalizedQuery),
          );
      const matchesStatus =
        statusFilter === "all" ||
        String(application.status || "").toLowerCase() === statusFilter;
      const matchesVisa =
        visaFilter === "all" || getVisaTypeLabel(application) === visaFilter;
      return matchesQuery && matchesStatus && matchesVisa;
    });
  }, [applications, query, statusFilter, visaFilter]);

  const locale = i18n.language === "kh" ? "km-KH" : "en-US";
  const applicationId = (application) =>
    application._id ?? application.id ?? application.applicationNumber;

  const statCards = [
    {
      label: t("totalApplications"),
      value: metrics.totalVisaApplications || 0,
      icon: BriefcaseBusiness,
      style: "bg-blue-50 text-blue-700",
    },
    {
      label: t("approvedApplications"),
      value: metrics.approvedApplications || 0,
      icon: ClipboardCheck,
      style: "bg-emerald-50 text-emerald-700",
    },
    {
      label: t("pendingDocuments"),
      value: metrics.pendingDocuments || 0,
      icon: FileText,
      style: "bg-amber-50 text-amber-700",
    },
    {
      label: t("rejectedApplications"),
      value: metrics.rejectedApplications || 0,
      icon: RotateCcw,
      style: "bg-rose-50 text-rose-700",
    },
  ];
  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <header className="workspace-page-heading"><div><h1>{t("dashboard")}</h1><p>Applications and document review</p></div><Link className="workspace-primary" to="/applications">{t("newApplication")}</Link></header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {statCards.map(({ label, value, icon: Icon, style }) => (
          <article
            key={label}
            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div>
              <p className="text-xs text-slate-500">{label}</p>
              <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
            </div>
          </article>
        ))}
      </section>
      <div className="min-w-0 space-y-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-bold text-slate-900">
                {t("applicationTrend")}
              </h2>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                {i18n.language === "kh" ? "៣០ ថ្ងៃចុងក្រោយ" : "Last 30 days"}
              </span>
            </div>
            {applicationTrend.length ? (
              <div className="mt-3 h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={applicationTrend}
                    margin={{ top: 8, right: 12, left: -20, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient
                        id="pendingFill"
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="1"
                      >
                        <stop
                          offset="0%"
                          stopColor="#2563eb"
                          stopOpacity={0.28}
                        />
                        <stop
                          offset="95%"
                          stopColor="#2563eb"
                          stopOpacity={0.02}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="#e2e8f0"
                    />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(value) =>
                        new Date(`${value}T00:00:00`).toLocaleDateString(
                          locale,
                          { month: "short", day: "numeric" },
                        )
                      }
                      interval={4}
                      tick={{ fontSize: 10 }}
                      stroke="#94a3b8"
                    />
                    <YAxis
                      allowDecimals={false}
                      tick={{ fontSize: 10 }}
                      stroke="#94a3b8"
                    />
                    <Tooltip
                      labelFormatter={(value) =>
                        new Date(`${value}T00:00:00`).toLocaleDateString(locale)
                      }
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Area
                      type="monotone"
                      dataKey="approved"
                      name={t("approvedApplications")}
                      stroke="#059669"
                      fill="#059669"
                      fillOpacity={0.12}
                      strokeWidth={2}
                    />
                    <Area
                      type="monotone"
                      dataKey="pending"
                      name={t("pending")}
                      stroke="#2563eb"
                      fill="url(#pendingFill)"
                      strokeWidth={2}
                    />
                    <Area
                      type="monotone"
                      dataKey="rejected"
                      name={t("rejected")}
                      stroke="#e11d48"
                      fill="#e11d48"
                      fillOpacity={0.08}
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="py-12 text-center text-sm text-slate-500">
                {loading ? t("loading") : t("noData")}
              </p>
            )}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-sm font-bold text-slate-900">
              {t("applicationStatus")}
            </h2>
            {visaStatusTotal > 0 ? (
              <div className="mt-2 flex items-center justify-center gap-4">
                <div className="relative h-32 w-32 shrink-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={visaStatusDistribution}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={36}
                        outerRadius={56}
                        paddingAngle={2}
                      >
                        {visaStatusDistribution.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(value) => [value, "Applications"]} />
                    </PieChart>
                  </ResponsiveContainer>
                  <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm font-bold text-slate-800">
                    {visaStatusTotal}
                  </span>
                </div>
                <ul className="space-y-2 text-xs text-slate-600">
                  {visaStatusDistribution.map((item) => (
                    <li key={item.name} className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      {item.name} · {item.value} ·{" "}
                      {formatNumber((item.value / visaStatusTotal) * 100)}%
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="py-10 text-center text-sm text-slate-500">
                {t("noData")}
              </p>
            )}
          </section>
        </div>

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4">
            <h2 className="text-base font-bold text-slate-900">
              {t("recentApplications")}
            </h2>
            <Link
              to="/applications"
              className="text-sm font-semibold text-blue-700 hover:text-blue-900"
            >
              {t("applications")} →
            </Link>
          </div>
          <div className="grid gap-2 border-b border-slate-200 bg-slate-50 p-3 md:grid-cols-[minmax(0,1fr)_150px_170px]">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("searchApplications")}
              aria-label={t("searchApplications")}
              className="min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label={t("filterByStatus")}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="all">{t("allStatuses")}</option>
              <option value="draft">{t("draft")}</option>
              <option value="under_verification">
                {t("underVerification")}
              </option>
              <option value="pending">{t("pending")}</option>
              <option value="verified">{t("verified")}</option>
              <option value="rejected">{t("rejected")}</option>
            </select>
            <select
              value={visaFilter}
              onChange={(event) => setVisaFilter(event.target.value)}
              aria-label={t("filterByVisaType")}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="all">{t("allVisaTypes")}</option>
              {visaTypes.map((visaType) => (
                <option
                  key={visaType.id ?? visaType._id ?? visaType.name}
                  value={visaType.name}
                >
                  {visaType.name}
                </option>
              ))}
            </select>
          </div>
          {dashboardError && (
            <p
              role="alert"
              className="border-b border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            >
              {dashboardError}
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-white text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">#</th>
                  <th className="px-4 py-3">{t("applicant")}</th>
                  <th className="px-4 py-3">{t("visaType")}</th>
                  <th className="px-4 py-3">{t("applicationDate")}</th>
                  <th className="px-4 py-3">{t("status")}</th>
                  <th className="px-4 py-3">{t("verify")}</th>
                  <th className="px-4 py-3 text-right">{t("actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredApplications.map((application, index) => {
                  const id = applicationId(application);
                  const status = String(
                    application.status || "PENDING",
                  ).toUpperCase();
                  const statusKey =
                    {
                      DRAFT: "draft",
                      UNDER_VERIFICATION: "underVerification",
                      PENDING: "pending",
                      VERIFIED: "verified",
                      REJECTED: "rejected",
                    }[status] || status;
                  const statusStyle =
                    status === "VERIFIED"
                      ? "bg-emerald-100 text-emerald-800"
                      : status === "REJECTED"
                        ? "bg-rose-100 text-rose-800"
                        : status === "DRAFT"
                          ? "bg-slate-100 text-slate-700"
                          : "bg-blue-100 text-blue-800";
                  return (
                    <tr key={id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 text-xs text-slate-400">
                        {index + 1}
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-semibold text-slate-900">
                          {application.applicant || "—"}
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          {application.applicationNumber || "—"}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-xs font-medium text-blue-800">
                        {getVisaTypeCode(application) ||
                          getVisaTypeLabel(application) ||
                          "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-600">
                        {application.createdAt
                          ? new Date(application.createdAt).toLocaleString(
                              locale,
                              { dateStyle: "medium", timeStyle: "short" },
                            )
                          : "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-medium ${statusStyle}`}
                        >
                          {t(statusKey)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {application.hasVerificationResult &&
                        application.verificationPercentage != null ? (
                          <ReadinessBar
                            value={application.verificationPercentage}
                          />
                        ) : (
                          <span className="text-xs text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          to={`/applications/${id}/results`}
                          className="text-xs font-semibold text-blue-700 hover:text-blue-900"
                        >
                          {t("viewReport")}
                        </Link>
                      </td>
                    </tr>
                  );
                })}
                {filteredApplications.length === 0 && (
                  <tr>
                    <td
                      colSpan="7"
                      className="px-4 py-8 text-center text-sm text-slate-500"
                    >
                      {loading ? t("loading") : t("noApplications")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
            <span>
              {filteredApplications.length} {t("applicationsShown")}
            </span>
            <span>{t("latestApplicationsNote")}</span>
          </div>
        </section>
      </div>
    </div>
  );
}

export default DashboardPage;
