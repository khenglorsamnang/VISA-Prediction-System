import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Clock3,
  Copy,
  Ellipsis,
  FileCheck2,
  FilePlus2,
  FileText,
  Filter,
  Globe2,
  History,
  Image,
  ListFilter,
  MapPin,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Star,
  Trash2,
  ArrowUpDown,
  X,
} from "lucide-react";
import { api } from "./pageUtils";
import { useTranslation } from "react-i18next";
import "./RequirementsPage.css";

const EMPTY_VISA = {
  countryId: "",
  category: "Tourist",
  entryType: "Single",
  validity: "90 Days",
  allowedStayDays: "15",
  visaClass: "S",
  applicantNationality: "KHM",
  additionalNotes: "",
};

const EMPTY_REQUIREMENT = {
  name: "",
  required: true,
  conditional: false,
  weight: "",
  description: "",
  acceptedFormats: "pdf,jpg,jpeg,png",
  maxFileSize: 10485760,
  displayOrder: 0,
  allowMultiple: false,
  status: "ACTIVE",
};

const POINTS_VALIDATION_MESSAGE = "Points must be between 0 and 100.";

const isValidPoints = (value) =>
  String(value).trim() !== "" &&
  Number.isFinite(Number(value)) &&
  Number(value) >= 0 &&
  Number(value) <= 100;

const PAGE_SIZE = 8;
const NEW_COUNTRY_DRAFT_ID = "__new_country__";
const CATEGORY_CODES = {
  tourist: "TOU",
  business: "BUS",
  student: "STU",
  work: "WORK",
  transit: "TRANSIT",
  family: "FAM",
};
const ENTRY_CODES = { Single: "SIN", Double: "DBL", Multiple: "MLT" };
const formatDate = (value, language) =>
  value
    ? new Intl.DateTimeFormat(language === "kh" ? "km-KH" : "en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(new Date(value))
    : language === "kh" ? "មិនមានកំណត់ត្រា" : "Not recorded";
const translateDuration = (value, t) => {
  const match = String(value || "").match(/^(\d+)\s+(Days?|Years?)$/i);
  if (!match) return value;
  const isYear = match[2].toLowerCase().startsWith("y");
  const key = `${match[1]}${isYear ? Number(match[1]) === 1 ? "Year" : "Years" : "Days"}`;
  return t(key, { defaultValue: value });
};
const categoryCode = (category) =>
  CATEGORY_CODES[String(category || "").toLowerCase()] ||
  String(category || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
const durationCode = (validity) => {
  const match = String(validity || "").match(/^(\d+)\s*(D|DAY|DAYS|Y|YEAR|YEARS)$/i);
  if (!match) return "";
  const unit = match[2].toUpperCase().startsWith("Y") ? "Y" : "D";
  return `${Number(match[1])}${unit}`;
};
const makeVisaCode = (form, countries) => {
  const country = countries.find((item) => item._id === form.countryId);
  const duration = durationCode(form.validity);
  const stayDays = Number(form.allowedStayDays);
  const nationality = String(form.applicantNationality || "").trim().toUpperCase();
  const visaClass = String(form.visaClass || "").trim().toUpperCase();
  if (
    !country ||
    !ENTRY_CODES[form.entryType] ||
    !duration ||
    !Number.isInteger(stayDays) ||
    stayDays < 1 ||
    !/^[A-Z]{3}$/.test(nationality) ||
    !/^[A-Z0-9]{1,4}$/.test(visaClass)
  )
    return "";
  return `VISA-${country.isoCode}-${categoryCode(form.category)}-${ENTRY_CODES[form.entryType]}-V${duration}-${stayDays}D-${visaClass}-${nationality}`;
};

function Toast({ message, type, onClose }) {
  const { t } = useTranslation();
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4500);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);
  return (
    <div className={`vreq-toast vreq-toast-${type}`} role={type === "error" ? "alert" : "status"}>
      {type === "error" ? <CircleAlert size={18} /> : <Check size={18} />}
      <span>{message}</span>
      <button type="button" aria-label={t("dismissNotification")} onClick={onClose}>
        <X size={16} />
      </button>
    </div>
  );
}

function Field({ label, children, className = "" }) {
  return (
    <label className={`vreq-field ${className}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function RequirementEditor({
  item,
  index,
  onChange,
  onRemove,
  canRemove,
}) {
  const { t } = useTranslation();
  return (
    <article className="vreq-requirement-editor">
      <div className="vreq-requirement-number">{String(index + 1).padStart(2, "0")}</div>
      <div className="vreq-requirement-fields vreq-requirement-fields-simple">
        <Field label={t("requirementName")}>
          <div className="vreq-input-icon">
            <FileText size={16} aria-hidden="true" />
            <input
              type="text"
              required
              maxLength={120}
              aria-label={t("requirementName")}
              value={item.name}
              placeholder={t("validPassportPlaceholder")}
              onChange={(event) => onChange({ ...item, name: event.target.value })}
            />
          </div>
        </Field>
        <Field label={t("points")}>
          <div className="vreq-input-icon">
            <Star size={16} aria-hidden="true" />
            <input
              type="number"
              min="0"
              max="100"
              step="0.01"
              required
              aria-label={t("points")}
              value={item.weight}
              onChange={(event) =>
                onChange({ ...item, weight: event.target.value })
              }
            />
          </div>
        </Field>
        {canRemove && (
          <button type="button" className="vreq-icon-button vreq-remove-requirement"
            aria-label={`${t("remove")} ${item.name || t("requirementName")}`} onClick={onRemove}>
            <Trash2 size={16} />
          </button>
        )}
      </div>
      <div className="vreq-requirement-details">
        <Field label="Accepted formats (PDF, JPG, JPEG, PNG)">
          <input
            required
            value={item.acceptedFormats}
            placeholder="pdf,jpg,jpeg,png"
            onChange={(event) =>
              onChange({ ...item, acceptedFormats: event.target.value })
            }
          />
        </Field>
        <Field label="Requirement status">
          <select
            value={item.status || "ACTIVE"}
            onChange={(event) =>
              onChange({ ...item, status: event.target.value })
            }
          >
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
          </select>
        </Field>
        <Field label="Requirement type">
          <select
            value={item.required ? "required" : "optional"}
            onChange={(event) =>
              onChange({ ...item, required: event.target.value === "required" })
            }
          >
            <option value="required">Required</option>
            <option value="optional">Optional</option>
          </select>
        </Field>
        <Field label="Additional notes">
          <input
            value={item.description || ""}
            onChange={(event) =>
              onChange({ ...item, description: event.target.value })
            }
          />
        </Field>
      </div>
    </article>
  );
}

export default function PremiumRequirementsPage() {
  const { t, i18n } = useTranslation();
  const [countries, setCountries] = useState([]);
  const [visaTypes, setVisaTypes] = useState([]);
  const [requirements, setRequirements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [visaForm, setVisaForm] = useState(EMPTY_VISA);
  const [requirementDrafts, setRequirementDrafts] = useState([
    { ...EMPTY_REQUIREMENT },
  ]);
  const [editingRequirementIndex, setEditingRequirementIndex] = useState(null);
  const [editingId, setEditingId] = useState("");
  const [selectedVisa, setSelectedVisa] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [menuId, setMenuId] = useState("");
  const [query, setQuery] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("active");
  const [page, setPage] = useState(1);
  const [sortNewest, setSortNewest] = useState(true);
  const [addCountryOpen, setAddCountryOpen] = useState(false);
  const [countryForm, setCountryForm] = useState({
    name: "",
    isoCode: "",
    flag: "",
    nationality: "",
  });
  const [toast, setToast] = useState(null);
  const [history, setHistory] = useState([]);
  const formRef = useRef(null);

  const notify = (message, type = "success") => setToast({ message, type });
  const refresh = async () => {
    const [countryResponse, visaResponse, requirementResponse] =
      await Promise.all([
        api.get("/countries"),
        api.get("/visa-types"),
        api.get("/requirements", { params: { includeArchived: true } }),
      ]);
    setCountries(countryResponse.data.countries || []);
    setVisaTypes(visaResponse.data.visaTypes || []);
    setRequirements(requirementResponse.data.requirements || []);
    return {
      visaTypes: visaResponse.data.visaTypes || [],
      requirements: requirementResponse.data.requirements || [],
    };
  };

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get("/countries"),
      api.get("/visa-types"),
      api.get("/requirements", { params: { includeArchived: true } }),
    ])
      .then(([countryResponse, visaResponse, requirementResponse]) => {
        if (!active) return;
        setCountries(countryResponse.data.countries || []);
        setVisaTypes(visaResponse.data.visaTypes || []);
        setRequirements(requirementResponse.data.requirements || []);
      })
      .catch((requestError) =>
        notify(
          requestError?.response?.data?.message ||
            t("unableLoadVisaRequirements"),
          "error",
        ),
      )
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [t]);

  useEffect(() => {
    if (!selectedVisa && !deleteTarget && !menuId) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setSelectedVisa(null);
        setDeleteTarget(null);
        setMenuId("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteTarget, menuId, selectedVisa]);

  const countriesById = useMemo(
    () => new Map(countries.map((country) => [country._id, country])),
    [countries],
  );
  const requirementsByVisa = useMemo(() => {
    const grouped = new Map();
    requirements.forEach((requirement) => {
      const visaId = String(
        requirement.visaTypeId?.toString?.() ||
          requirement.visaType?.toString?.() ||
          requirement.visaTypeId ||
          requirement.visaType ||
          "",
      );
      if (!grouped.has(visaId)) grouped.set(visaId, []);
      grouped.get(visaId).push(requirement);
    });
    return grouped;
  }, [requirements]);
  const visibleVisas = useMemo(() => {
    const search = query.trim().toLowerCase();
    return visaTypes
      .filter((visa) => {
        return (
          (statusFilter === "all" ||
            (statusFilter === "active" ? visa.active : !visa.active)) &&
          (!search ||
            `${visa.visaTypeCode} ${visa.name} ${visa.category} ${visa.destination}`
              .toLowerCase()
              .includes(search)) &&
          (!countryFilter || visa.countryId === countryFilter) &&
          (!typeFilter || visa.category === typeFilter)
        );
      })
      .sort((left, right) => {
        const leftTime = new Date(left.updatedAt || left.createdAt || 0).getTime();
        const rightTime = new Date(right.updatedAt || right.createdAt || 0).getTime();
        return sortNewest ? rightTime - leftTime : leftTime - rightTime;
      });
  }, [countryFilter, query, sortNewest, statusFilter, typeFilter, visaTypes]);
  const pageCount = Math.max(1, Math.ceil(visibleVisas.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const currentVisas = visibleVisas.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );
  const totalRequirements = requirements.filter(
    (item) => item.status !== "ARCHIVED",
  ).length;
  const generatedCode = makeVisaCode(
    {
      ...visaForm,
      countryId: addCountryOpen ? NEW_COUNTRY_DRAFT_ID : visaForm.countryId,
    },
    addCountryOpen
      ? [
          ...countries,
          { _id: NEW_COUNTRY_DRAFT_ID, isoCode: countryForm.isoCode },
        ]
      : countries,
  );
  const existingCountryForDraft = addCountryOpen && countryForm.isoCode
    ? countries.find(
        (country) =>
          country.isoCode.toUpperCase() === countryForm.isoCode.toUpperCase(),
      )
    : null;
  const categories = [...new Set(visaTypes.map((item) => item.category).filter(Boolean))];
  const selectedCountry = countriesById.get(selectedVisa?.countryId);
  const selectedRequirements = selectedVisa
    ? (requirementsByVisa.get(selectedVisa._id) || []).filter(
        (item) => item.status !== "ARCHIVED",
      )
    : [];
  const activeMaximumPoints = requirementDrafts
    .filter((item) => !["INACTIVE", "ARCHIVED"].includes(item.status))
    .reduce((total, item) => total + (Number(item.weight) || 0), 0);
  const liveRemainingPoints = Math.max(
    0,
    100 -
      requirementDrafts.reduce(
        (total, item, index) =>
          index === editingRequirementIndex ||
          ["INACTIVE", "ARCHIVED"].includes(item.status)
            ? total
            : total + (Number(item.weight) || 0),
        0,
      ),
  );

  const setDraft = (index, item) =>
    setRequirementDrafts((current) => {
      setEditingRequirementIndex(index);
      const previous = current[index];
      const next = current.map((requirement, itemIndex) =>
        itemIndex === index ? item : requirement,
      );
      if (
        previous &&
        (previous.weight !== item.weight || previous.status !== item.status)
      ) {
        const otherActivePoints = next.reduce(
          (total, requirement, itemIndex) =>
            itemIndex === index ||
            ["INACTIVE", "ARCHIVED"].includes(requirement.status)
              ? total
              : total + (Number(requirement.weight) || 0),
          0,
        );
        const remainingPoints = Math.max(0, 100 - otherActivePoints);
        if (
          !isValidPoints(item.weight)
        ) {
          notify(POINTS_VALIDATION_MESSAGE, "error");
        } else if (
          ["INACTIVE", "ARCHIVED"].includes(item.status)
            ? false
            : Number(item.weight) > remainingPoints
        ) {
          notify(
            `Active requirements total cannot exceed 100 points. Remaining points: ${remainingPoints.toFixed(2).replace(/\.00$/, "")}.`,
            "error",
          );
        }
      }
      return next;
    });

  const beginEdit = async (visa) => {
    setMenuId("");
    if (!visa.active) {
      notify(t("activateVisaBeforeEdit"), "error");
      return;
    }
    const items = (requirementsByVisa.get(visa._id) || [])
      .sort((left, right) => (left.displayOrder || 0) - (right.displayOrder || 0))
      .map((item) => ({
        ...item,
        acceptedFormats: (item.acceptedFormats || []).join(","),
        maxFileSize: item.maxFileSize || 10485760,
        displayOrder: item.displayOrder || 0,
      }));
    setAddCountryOpen(false);
    setEditingId(visa._id);
    setVisaForm({
      countryId: visa.countryId,
      category: visa.category,
      entryType: visa.entryType,
      validity: visa.validity,
      allowedStayDays: String(visa.allowedStayDays),
      visaClass: visa.visaClass,
      applicantNationality: visa.applicantNationality,
      additionalNotes: visa.additionalNotes || "",
      effectiveDate: visa.effectiveDate?.slice(0, 10) || "",
      officialSourceUrl: visa.officialSourceUrl || "",
    });
    setRequirementDrafts(items.length ? items : [{ ...EMPTY_REQUIREMENT }]);
    const invalidExistingPoints = items.filter(
      (item) => !isValidPoints(item.weight),
    );
    const existingActiveTotal = items
      .filter((item) => !["INACTIVE", "ARCHIVED"].includes(item.status))
      .reduce((total, item) => total + (Number(item.weight) || 0), 0);
    if (invalidExistingPoints.length) {
      notify(
        "Existing Active requirement points must be between 0 and 100. Please correct them before saving.",
        "error",
      );
    } else if (existingActiveTotal > 100) {
      notify(
        `Existing Active requirements exceed 100 points. Remaining points: ${Math.max(0, 100 - existingActiveTotal).toFixed(2).replace(/\.00$/, "")}. Please correct them before saving.`,
        "error",
      );
    }
    setSelectedVisa(null);
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const resetForm = () => {
    setEditingId("");
    setEditingRequirementIndex(null);
    setAddCountryOpen(false);
    setCountryForm({ name: "", isoCode: "", flag: "", nationality: "" });
    setVisaForm(EMPTY_VISA);
    setRequirementDrafts([{ ...EMPTY_REQUIREMENT }]);
  };

  const buildRequirementPayload = (item, index, existingRequirement) => {
    const requestedFormats = item.acceptedFormats
      .split(",")
      .map((format) => format.trim().toLowerCase().replace(/^\./, ""))
      .filter(Boolean);
    const photo = /photo|photograph/i.test(`${item.name} ${item.code || ""}`);
    const supportedFormats = new Set(["pdf", "jpg", "jpeg", "png"]);
    if (
      !requestedFormats.length ||
      requestedFormats.some((format) => !supportedFormats.has(format)) ||
      (photo && requestedFormats.includes("pdf"))
    ) {
      throw new Error(
        `${item.name}: Choose supported formats (PDF, JPG, JPEG, PNG). Photos accept JPG, JPEG, and PNG only.`,
      );
    }
    const formats = [...new Set(requestedFormats)];
    const payload = {
      name: item.name.trim(),
      description: item.description || "",
      code: item.code || item.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
      weight: Number(item.weight),
      scoringMode: item.scoringMode || "FIXED",
      minimumEarnedPoints: String(item.minimumEarnedPoints ?? 0),
      maximumEarnedPoints: String(item.weight ?? 0),
      qualityScorer: item.scoringMode === "DYNAMIC" ? (item.qualityScorer || "MANUAL_RUBRIC") : "FIXED",
      qualityScorerVersion: item.qualityScorerVersion || "1.0",
      mappingVersion: item.mappingVersion || (item.qualityScorer === "ABA_STATEMENT_V1" ? "bank-linear-1-to-10-v1" : null),
      qualityRubric: typeof item.qualityRubric === "string" ? JSON.parse(item.qualityRubric || "null") : item.qualityRubric || null,
      applicabilityRule: typeof item.applicabilityRule === "string" ? JSON.parse(item.applicabilityRule || "null") : item.applicabilityRule || null,
      evidenceRules: typeof item.evidenceRules === "string" ? JSON.parse(item.evidenceRules || "{}") : item.evidenceRules || {},
      alternativeGroupId: item.alternativeGroupId || null,
      documentSelectionStrategy: item.documentSelectionStrategy || "REVIEWER_SELECTED_PRIMARY",
      requiredForFinalAssessment: item.requiredForFinalAssessment ?? Boolean(item.required),
      sourceKind: item.sourceKind || "INTERNAL",
      sourceStatus: item.sourceStatus || "UNVERIFIED",
      sourceReviewedAt: item.sourceReviewedAt || null,
      required: Boolean(item.required),
      conditional: Boolean(item.conditional),
      acceptedFormats: formats,
      maxFileSize: Number(item.maxFileSize) || 10485760,
      displayOrder: index,
      allowMultiple: Boolean(item.allowMultiple),
      status: item.status || "ACTIVE",
      source: item.source || "",
      effectiveFrom: item.effectiveFrom || "",
    };
    const requirementId = item._id || existingRequirement?._id;
    return { ...payload, ...(requirementId ? { requirementId } : {}) };
  };

  const submitVisa = async (event) => {
    event.preventDefault();
    if (
      addCountryOpen &&
      (!countryForm.name.trim() || !/^[A-Z]{2}$/.test(countryForm.isoCode))
    ) {
      notify(t("validCountryAndIso"), "error");
      return;
    }
    if (!addCountryOpen && !visaForm.countryId) {
      notify(t("chooseCountryBeforeSave"), "error");
      return;
    }
    if (!generatedCode) {
      notify(t("checkVisaDetails"), "error");
      return;
    }
    if (
      requirementDrafts.some(
        (item) =>
          !item.name.trim()
      )
    ) {
      notify(
        t("completeEachRequirement"),
        "error",
      );
      return;
    }
    if (
      requirementDrafts.some(
        (item) => !isValidPoints(item.weight),
      )
    ) {
      notify(POINTS_VALIDATION_MESSAGE, "error");
      return;
    }
    const activeMaximumPoints = requirementDrafts
      .filter((item) => !["INACTIVE", "ARCHIVED"].includes(item.status))
      .reduce((total, item) => total + (Number(item.weight) || 0), 0);
    if (activeMaximumPoints > 100) {
      const offendingIndex = requirementDrafts.findIndex((item, index) => {
        if (["INACTIVE", "ARCHIVED"].includes(item.status)) return false;
        const otherActivePoints = requirementDrafts.reduce(
          (total, other, otherIndex) =>
            otherIndex === index ||
            ["INACTIVE", "ARCHIVED"].includes(other.status)
              ? total
              : total + (Number(other.weight) || 0),
          0,
        );
        return Number(item.weight) > 100 - otherActivePoints;
      });
      const offendingItem = offendingIndex >= 0
        ? requirementDrafts[offendingIndex]
        : null;
      const remainingPoints = offendingItem
        ? Math.max(
            0,
            100 -
              requirementDrafts.reduce(
                (total, item, index) =>
                  index === offendingIndex ||
                  ["INACTIVE", "ARCHIVED"].includes(item.status)
                    ? total
                    : total + (Number(item.weight) || 0),
                0,
              ),
          )
        : 0;
      notify(
        `Active requirements total cannot exceed 100 points. Remaining points: ${remainingPoints.toFixed(2).replace(/\.00$/, "")}.`,
        "error",
      );
      return;
    }
    if (!requirementDrafts.length) {
      notify(t("addAtLeastOneRequirement"), "error");
      return;
    }
    const requirementNames = requirementDrafts.map((item) =>
      item.name.trim().toLowerCase(),
    );
    if (new Set(requirementNames).size !== requirementNames.length) {
      notify(t("requirementNamesUnique"), "error");
      return;
    }
    const matchingVisa = visaTypes.find(
      (visa) =>
        visa._id !== editingId && visa.visaTypeCode === generatedCode,
    );

    setSaving(true);
    let newlyCreatedCountry = null;
    let savedCode = generatedCode;
    try {
      let selectedCountry = countriesById.get(visaForm.countryId);
      if (addCountryOpen) {
        const isoCode = countryForm.isoCode.toUpperCase();
        const existingCountry = countries.find(
          (country) => country.isoCode.toUpperCase() === isoCode,
        );
        if (existingCountry) {
          selectedCountry = existingCountry;
          if (!existingCountry.active) {
            const countryResponse = await api.put(
              `/countries/${existingCountry._id}`,
              {
                name: countryForm.name.trim(),
                isoCode,
                flag: countryForm.flag,
                nationality: countryForm.nationality.toUpperCase(),
                active: true,
              },
            );
            newlyCreatedCountry = countryResponse.data.country;
            selectedCountry = newlyCreatedCountry;
          }
        } else {
          const countryResponse = await api.post("/countries", {
            ...countryForm,
            name: countryForm.name.trim(),
            isoCode,
            nationality: countryForm.nationality.toUpperCase(),
          });
          newlyCreatedCountry = countryResponse.data.country;
          selectedCountry = newlyCreatedCountry;
        }
      }
      if (!selectedCountry) {
        throw new Error(t("chooseActiveCountry"));
      }
      savedCode =
        makeVisaCode(
          { ...visaForm, countryId: selectedCountry._id },
          [...countries, selectedCountry],
        ) || generatedCode;
      const payload = {
        ...visaForm,
        visaTypeCode: savedCode,
        countryId: selectedCountry._id,
        name: `${selectedCountry.name} ${visaForm.category} Visa`,
        allowedStayDays: Number(visaForm.allowedStayDays),
        validityDays: Number(durationCode(visaForm.validity).replace(/[DY]/, "")) *
          (durationCode(visaForm.validity).endsWith("Y") ? 365 : 1),
      };
      const visaId = editingId || matchingVisa?._id || "";
      const previousRequirements = requirementsByVisa.get(visaId) || [];
      const claimedRequirementIds = new Set();
      const matchedRequirements = requirementDrafts.map((item) => {
        const existing =
          previousRequirements.find(
            (requirement) =>
              requirement._id === item._id &&
              !claimedRequirementIds.has(requirement._id),
          ) ||
          previousRequirements
            .filter(
              (requirement) =>
                requirement.name.trim().toLowerCase() ===
                  item.name.trim().toLowerCase() &&
                !claimedRequirementIds.has(requirement._id),
            )
            .sort(
              (left, right) =>
                Number(right.status !== "ARCHIVED") -
                Number(left.status !== "ARCHIVED"),
            )[0];
        if (existing) claimedRequirementIds.add(existing._id);
        return existing;
      });
      const requirementsPayload = requirementDrafts.map((item, index) =>
        buildRequirementPayload(item, index, matchedRequirements[index]),
      );
      const savePayload = {
        ...payload,
        active: true,
        requirements: requirementsPayload,
      };
      const saveResponse = visaId
        ? await api.put(`/visa-types/${visaId}/requirements`, savePayload)
        : await api.post("/visa-types/with-requirements", savePayload);
      const savedVisaId = saveResponse.data.visaType?._id;
      if (!savedVisaId) {
        throw new Error("The API did not return the saved VISA Type");
      }
      const reloaded = await refresh();
      const reloadedVisa = reloaded.visaTypes.find(
        (item) => item._id === savedVisaId && item.visaTypeCode === savedCode,
      );
      const reloadedRequirements = reloaded.requirements.filter(
        (item) => item.visaTypeId === savedVisaId,
      );
      const persistedByName = new Map(
        reloadedRequirements.map((item) => [item.name.trim().toLowerCase(), item]),
      );
      const missingRequirement = requirementsPayload.find((expected) => {
        const persisted = persistedByName.get(expected.name.trim().toLowerCase());
        return (
          !persisted ||
          persisted.required !== expected.required ||
          persisted.maxFileSize !== expected.maxFileSize ||
          persisted.status !== expected.status ||
          expected.acceptedFormats.some(
            (format) => !persisted.acceptedFormats?.includes(format),
          )
        );
      });
      if (!reloadedVisa || missingRequirement || reloadedRequirements.length < requirementsPayload.length) {
        throw new Error(
          missingRequirement
            ? `Requirement '${missingRequirement.name}' was not persisted correctly`
            : "The saved VISA Type and its requirements could not be verified after reloading",
        );
      }
      resetForm();
      setAddCountryOpen(false);
      setCountryForm({ name: "", isoCode: "", flag: "", nationality: "" });
      notify(
        editingId
          ? t("visaRequirementsUpdated")
          : matchingVisa && !matchingVisa.active
            ? t("archivedVisaRestored")
            : matchingVisa
            ? t("existingVisaRequirementsUpdated")
            : t("visaRequirementsCreated"),
      );
    } catch (requestError) {
      const failedRequirement = requestError?.response?.data?.failedRequirement;
      const requestMessage =
        requestError?.response?.data?.message ||
        requestError.message ||
        t("unableSaveVisaRequirements");
      const detailedMessage = failedRequirement
        ? `${failedRequirement}: ${requestMessage}`
        : requestMessage;
      if (newlyCreatedCountry) {
        try {
          await refresh();
        } catch {
          notify(
            t("countrySavedVisaFailed", { country: newlyCreatedCountry.name, code: savedCode, error: detailedMessage || t("pleaseRetry") }),
            "error",
          );
          return;
        }
      }
      notify(
        newlyCreatedCountry
          ? t("countrySavedFormFailed", { country: newlyCreatedCountry.name, error: detailedMessage || t("pleaseRetry") })
          : detailedMessage,
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  const duplicateVisa = async (visa) => {
    setMenuId("");
    setSaving(true);
    try {
      const response = await api.post(`/visa-types/${visa._id}/duplicate`);
      await refresh();
      notify(t("createdVisaCode", { code: response.data.visaType.visaTypeCode }));
    } catch (requestError) {
      notify(requestError?.response?.data?.message || t("unableDuplicateVisa"), "error");
    } finally {
      setSaving(false);
    }
  };

  const toggleVisa = async (visa) => {
    setMenuId("");
    setSaving(true);
    try {
      const response = visa.active
        ? await api.patch(`/visa-types/${visa._id}/archive`)
        : await api.put(`/visa-types/${visa._id}`, {
            countryId: visa.countryId,
            category: visa.category,
            entryType: visa.entryType,
            validity: visa.validity,
            validityDays: visa.validityDays,
            allowedStayDays: visa.allowedStayDays,
            visaClass: visa.visaClass,
            applicantNationality: visa.applicantNationality,
            additionalNotes: visa.additionalNotes || "",
            active: true,
          });
      setVisaTypes((current) =>
        current.map((item) =>
          item._id === visa._id
            ? { ...item, ...response.data.visaType }
            : item,
        ),
      );
      notify(response.data.message || t("visaStatusUpdated"));
    } catch (requestError) {
      notify(requestError?.response?.data?.message || t("unableUpdateVisaStatus"), "error");
    } finally {
      setSaving(false);
    }
  };

  const deleteVisa = async () => {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      const response = await api.delete(`/visa-types/${deleteTarget._id}`);
      setDeleteTarget(null);
      setSelectedVisa(null);
      await refresh();
      notify(response.data.message || t("visaCodeDeleted"));
    } catch (requestError) {
      notify(requestError?.response?.data?.message || t("unableDeleteVisa"), "error");
    } finally {
      setSaving(false);
    }
  };

  const loadHistory = async (requirement) => {
    try {
      const response = await api.get(`/requirements/${requirement._id}/history`);
      setHistory(response.data.versions || []);
    } catch (requestError) {
      notify(requestError?.response?.data?.message || t("unableLoadHistory"), "error");
    }
  };

  const updateQuery = (value, setter) => {
    setter(value);
    setPage(1);
  };

  return (
    <div className="vreq-page">
      <header className="vreq-page-header">
        <div>
          <h1>{t("visaRequirementsTitle")}</h1>
          <p>{t("manageVisaPrograms")}</p>
        </div>
        <button
          type="button"
          className="vreq-button vreq-button-primary"
          onClick={() => formRef.current?.scrollIntoView({ behavior: "smooth" })}
        >
          <Plus size={17} /> {t("createVisaCode")}
        </button>
      </header>

      <section className="vreq-create-card" ref={formRef}>
        <div className="vreq-section-heading">
          <div>
            <h2>{editingId ? t("editVisaRequirements") : t("createVisaRequirements")}</h2>
            <p>
              {t("configureTravelDetails")}
            </p>
          </div>
          {editingId && (
            <button
              type="button"
              className="vreq-button vreq-button-secondary vreq-cancel-edit"
              onClick={resetForm}
            >
              <X size={16} /> {t("cancelEdit")}
            </button>
          )}
        </div>

        <form onSubmit={submitVisa}>
          <div className="vreq-form-grid">
            <Field label={t("country")}>
              <select
                required
                value={addCountryOpen ? NEW_COUNTRY_DRAFT_ID : visaForm.countryId}
                onChange={(event) => {
                  const addingCountry =
                    event.target.value === NEW_COUNTRY_DRAFT_ID;
                  setAddCountryOpen(addingCountry);
                  if (addingCountry) {
                    setCountryForm({
                      name: "",
                      isoCode: "",
                      flag: "",
                      nationality: "",
                    });
                    setVisaForm((current) => ({ ...current, countryId: "" }));
                  } else {
                    setVisaForm((current) => ({
                      ...current,
                      countryId: event.target.value,
                    }));
                  }
                }}
              >
                <option value="">{t("selectCountry")}</option>
                {countries
                  .filter((country) => country.active)
                  .map((country) => (
                    <option key={country._id} value={country._id}>
                      {country.name} ({country.isoCode})
                    </option>
                  ))}
                <option value={NEW_COUNTRY_DRAFT_ID}>{t("addNewCountry")}</option>
              </select>
            </Field>
            {addCountryOpen && (
              <>
                <Field label={t("countryName")}>
                  <input
                    required
                    maxLength={100}
                    value={countryForm.name}
                    onChange={(event) =>
                      setCountryForm((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                  />
                </Field>
                <Field label={t("iso2Code")}>
                  <input
                    required
                    minLength={2}
                    maxLength={2}
                    pattern="[A-Za-z]{2}"
                    value={countryForm.isoCode}
                    onChange={(event) =>
                      setCountryForm((current) => ({
                        ...current,
                        isoCode: event.target.value.toUpperCase(),
                      }))
                    }
                  />
                </Field>
                <Field label={t("countryNationalityCode")}>
                  <input
                    maxLength={3}
                    value={countryForm.nationality}
                    onChange={(event) =>
                      setCountryForm((current) => ({
                        ...current,
                        nationality: event.target.value.toUpperCase(),
                      }))
                    }
                    placeholder={t("exampleKhm")}
                  />
                </Field>
                <p className="vreq-country-submit-note">
                  {existingCountryForDraft
                    ? `${existingCountryForDraft.name} ${t("reusedCountry")}`
                    : t("newCountryVisaChecklistSave")}
                </p>
              </>
            )}
            <Field label={t("travelPurpose")}>
              <select
                value={visaForm.category}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    category: event.target.value,
                  }))
                }
              >
                {["Tourist", "Business", "Student", "Work", "Transit", "Family"].map(
                  (item) => (
                    <option key={item} value={item}>
                      {t(item.toLowerCase())}
                    </option>
                  ),
                )}
              </select>
            </Field>
            <Field label={t("visaDuration")}>
              <select
                value={visaForm.validity}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    validity: event.target.value,
                  }))
                }
              >
                {["30 Days", "60 Days", "90 Days", "180 Days", "1 Year", "2 Years"].map(
                  (item) => (
                    <option key={item}>{translateDuration(item, t)}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label={t("allowedStay")}>
              <div className="vreq-input-suffix">
                <input
                  required
                  type="number"
                  min="1"
                  max="3650"
                  value={visaForm.allowedStayDays}
                  onChange={(event) =>
                    setVisaForm((current) => ({
                      ...current,
                      allowedStayDays: event.target.value,
                    }))
                  }
                />
                <span>{t("days")}</span>
              </div>
            </Field>
            <Field label={t("entryType")}>
              <select
                value={visaForm.entryType}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    entryType: event.target.value,
                  }))
                }
              >
                {Object.keys(ENTRY_CODES).map((item) => (
                  <option key={item}>{t(item.toLowerCase())}</option>
                ))}
              </select>
            </Field>
            <Field label={t("nationalityCode")}>
              <input
                required
                pattern="[A-Za-z]{3}"
                maxLength={3}
                value={visaForm.applicantNationality}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    applicantNationality: event.target.value.toUpperCase(),
                  }))
                }
                placeholder="KHM"
              />
            </Field>
            <Field label={t("visaClass")}>
              <input
                required
                pattern="[A-Za-z0-9]{1,4}"
                maxLength={4}
                value={visaForm.visaClass}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    visaClass: event.target.value.toUpperCase(),
                  }))
                }
                placeholder="S"
              />
            </Field>
            <Field label={t("effectiveDate")}>
              <input
                type="date"
                value={visaForm.effectiveDate || ""}
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    effectiveDate: event.target.value,
                  }))
                }
              />
            </Field>
            <Field label={t("officialSourceUrl")}>
              <input
                type="url"
                value={visaForm.officialSourceUrl || ""}
                placeholder="https://..."
                onChange={(event) =>
                  setVisaForm((current) => ({
                    ...current,
                    officialSourceUrl: event.target.value,
                  }))
                }
              />
            </Field>
          </div>

          <div className="vreq-code-preview">
            <div>
              <span className="vreq-code-label">{t("generatedVisaCode")}</span>
              <code>{generatedCode || t("selectValidDetails")}</code>
            </div>
            <span className="vreq-code-lock">
              <ShieldCheck size={15} /> {t("uniqueCodeChecked")}
            </span>
          </div>

          <div className="vreq-requirement-heading">
            <div>
              <h3>{t("documentRequirements")}</h3>
              <p>Changes apply to new applications. Existing cases keep their saved scoring snapshot until explicitly recalculated.</p>
            </div>
            <button
              type="button"
              className="vreq-button vreq-button-primary"
              onClick={() =>
                setRequirementDrafts((current) => [
                  ...current,
                  { ...EMPTY_REQUIREMENT, displayOrder: current.length },
                ])
              }
            >
              <Plus size={16} /> {t("addRequirement")}
            </button>
          </div>
          <div className="vreq-requirement-list">
            {requirementDrafts.map((item, index) => (
              <RequirementEditor
                key={item._id || `new-${index}`}
                item={item}
                index={index}
                canRemove={requirementDrafts.length > 1}
                onChange={(next) => setDraft(index, next)}
                onRemove={() =>
                  setRequirementDrafts((current) =>
                    current.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              />
            ))}
          </div>
          <div className="vreq-point-summary">
            <span>Active maximum points</span>
            <strong>
              {activeMaximumPoints.toFixed(2).replace(/\.00$/, "")} / 100 · Remaining: {liveRemainingPoints.toFixed(2).replace(/\.00$/, "")}
            </strong>
          </div>

          <Field label={t("additionalNotes")} className="vreq-notes-field">
            <textarea
              rows={3}
              maxLength={2000}
              value={visaForm.additionalNotes || ""}
              placeholder={t("optionalInternalGuidance")}
              onChange={(event) =>
                setVisaForm((current) => ({
                  ...current,
                  additionalNotes: event.target.value,
                }))
              }
            />
            <small>{(visaForm.additionalNotes || "").length}/2000 {t("countCharacters")}</small>
          </Field>

          <div className="vreq-form-actions">
            <button
              type="button"
              className="vreq-button vreq-button-secondary"
              onClick={resetForm}
              disabled={saving}
            >
              {t("reset")}
            </button>
            <button
              type="submit"
              className="vreq-button vreq-button-primary"
              disabled={saving || !generatedCode}
            >
              {saving ? t("saving") : editingId ? t("saveChanges") : t("createVisaRequirementsButton")}
            </button>
          </div>
        </form>
      </section>


      <section className="vreq-list-card">
        <div className="vreq-list-header">
          <div>
            <h2>{t("visaCodeDirectory")}</h2>
            <p>{t("browseManagePrograms")}</p>
          </div>
          <div className="vreq-record-count">
            <span>{visibleVisas.length}</span> {t("records")}
          </div>
        </div>
        <div className="vreq-toolbar">
          <label className="vreq-search">
            <Search size={17} />
            <input
              value={query}
              onChange={(event) => updateQuery(event.target.value, setQuery)}
              placeholder={t("searchVisaCountry")}
              aria-label={t("searchVisaCountry")}
            />
            {query && (
              <button
                type="button"
                aria-label={t("clearSearch")}
                onClick={() => updateQuery("", setQuery)}
              >
                <X size={15} />
              </button>
            )}
          </label>
          <label className="vreq-filter">
            <Filter size={15} />
            <select
              value={countryFilter}
              aria-label={t("filterCountry")}
              onChange={(event) =>
                updateQuery(event.target.value, setCountryFilter)
              }
            >
              <option value="">{t("allCountries")}</option>
              {countries.map((country) => (
                <option key={country._id} value={country._id}>
                  {country.name}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </label>
          <label className="vreq-filter">
            <ListFilter size={15} />
            <select
              value={typeFilter}
              aria-label={t("filterVisaType")}
              onChange={(event) => updateQuery(event.target.value, setTypeFilter)}
            >
              <option value="">{t("allPurposes")}</option>
              {[...new Set([...categories, "Tourist", "Business", "Student", "Work", "Transit", "Family"])].map(
                (category) => (
                  <option key={category} value={category}>
                    {t(category.toLowerCase(), { defaultValue: category })}
                  </option>
                ),
              )}
            </select>
            <ChevronDown size={14} />
          </label>
          <label className="vreq-filter">
            <ShieldCheck size={15} />
            <select
              value={statusFilter}
              aria-label={t("filterVisaStatus")}
              onChange={(event) =>
                updateQuery(event.target.value, setStatusFilter)
              }
            >
              <option value="active">{t("activeOnly")}</option>
              <option value="all">{t("allStatuses")}</option>
              <option value="archived">{t("archivedOnly")}</option>
            </select>
            <ChevronDown size={14} />
          </label>
          <button
            type="button"
            className="vreq-sort-button"
            onClick={() => setSortNewest((current) => !current)}
            aria-label={t("sortByUpdated")}
          >
            {sortNewest ? <ArrowDown size={16} /> : <ArrowUp size={16} />}
            {t("latestUpdated")}
          </button>
        </div>

        {loading ? (
          <div className="vreq-skeleton-list" aria-label={t("loadingVisaCodes")}>
            {Array.from({ length: 5 }, (_, index) => (
              <div className="vreq-skeleton-row" key={index}>
                <span />
                <span />
                <span />
                <span />
              </div>
            ))}
          </div>
        ) : currentVisas.length ? (
          <>
            <div className="vreq-table-wrap" tabIndex={0} role="region" aria-label="Visa program directory">
              <table className="vreq-table">
                <thead>
                  <tr>
                    <th>{t("visaCode")}</th>
                    <th>{t("countryColumn")}</th>
                    <th>{t("travelPurposeColumn")}</th>
                    <th>{t("requirementsColumn")}</th>
                    <th>{t("lastUpdated")}</th>
                    <th>{t("status")}</th>
                    <th><span className="vreq-visually-hidden">{t("actions")}</span></th>
                  </tr>
                </thead>
                <tbody>
                  {currentVisas.map((visa) => {
                    const country = countriesById.get(visa.countryId);
                    const count = (requirementsByVisa.get(visa._id) || []).filter(
                      (item) => item.status !== "ARCHIVED",
                    ).length;
                    return (
                      <tr key={visa._id}>
                        <td>
                          <button
                            type="button"
                            className="vreq-code-link"
                            onClick={() => setSelectedVisa(visa)}
                          >
                            {visa.visaTypeCode}
                          </button>
                          <small>{visa.name}</small>
                        </td>
                        <td>
                          <span className="vreq-country-cell">
                            
                            <span>
                              {country?.name || visa.destination}
                              <small>{visa.countryCode}</small>
                            </span>
                          </span>
                        </td>
                        <td>
                          <span className="vreq-type-pill">{t(visa.category.toLowerCase(), { defaultValue: visa.category })}</span>
                          <small>{t(visa.entryType.toLowerCase())} {t("entryLabel")} · {translateDuration(visa.validity, t)}</small>
                        </td>
                        <td>
                          <span className="vreq-count">
                            <FileCheck2 size={15} /> {count}
                          </span>
                        </td>
                        <td>
                          <span className="vreq-updated">
                            <Clock3 size={14} /> {formatDate(visa.updatedAt || visa.createdAt, i18n.language)}
                          </span>
                        </td>
                        <td>
                          <span className={`vreq-status ${visa.active ? "is-active" : "is-inactive"}`}>
                            <i /> {visa.active ? t("active") : t("archived")}
                          </span>
                        </td>
                        <td>
                          <div className="vreq-row-menu-wrap">
                            <button
                              type="button"
                              className="vreq-icon-button"
                              aria-label={`${t("actionsFor")} ${visa.visaTypeCode}`}
                              aria-haspopup="menu"
                              aria-expanded={menuId === visa._id}
                              onClick={() =>
                                setMenuId((current) =>
                                  current === visa._id ? "" : visa._id,
                                )
                              }
                            >
                              <Ellipsis size={19} />
                            </button>
                            {menuId === visa._id && (
                              <div className="vreq-action-menu" role="menu">
                                <button role="menuitem" type="button" onClick={() => setSelectedVisa(visa)}>
                                  <FileCheck2 size={15} /> {t("viewDetails")}
                                </button>
                                <button role="menuitem" type="button" onClick={() => beginEdit(visa)}>
                                  <Pencil size={15} /> {t("edit")}
                                </button>
                                <button role="menuitem" type="button" onClick={() => duplicateVisa(visa)}>
                                  <Copy size={15} /> {t("duplicate")}
                                </button>
                                <button role="menuitem" type="button" onClick={() => toggleVisa(visa)}>
                                  <ShieldCheck size={15} /> {visa.active ? t("archive") : t("activate")}
                                </button>
                                <button
                                  role="menuitem"
                                  type="button"
                                  className="is-danger"
                                  onClick={() => {
                                    setMenuId("");
                                    setDeleteTarget(visa);
                                  }}
                                >
                                  <Trash2 size={15} /> {t("delete")}
                                </button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="vreq-pagination">
              <span>
                {t("showing")} <strong>{(currentPage - 1) * PAGE_SIZE + 1}</strong>–
                <strong>{Math.min(currentPage * PAGE_SIZE, visibleVisas.length)}</strong> of{" "}
                <strong>{visibleVisas.length}</strong>
              </span>
              <div>
                <button
                  type="button"
                  aria-label={t("previousPage")}
                  disabled={currentPage <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  <ChevronLeft size={17} />
                </button>
                <span>{t("page")} {currentPage} {t("of")} {pageCount}</span>
                <button
                  type="button"
                  aria-label={t("nextPage")}
                  disabled={currentPage >= pageCount}
                  onClick={() =>
                    setPage((current) => Math.min(pageCount, current + 1))
                  }
                >
                  <ChevronRight size={17} />
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="vreq-empty-state">
            <div><Search size={21} /></div>
            <h3>{visaTypes.length ? t("noMatchingVisaCodes") : t("noVisaCodesConfigured")}</h3>
            <p>
              {visaTypes.length
                ? t("tryDifferentSearch")
                : t("createProfileDirectory")}
            </p>
            {visaTypes.length > 0 && (
              <button
                type="button"
                className="vreq-button vreq-button-secondary"
                onClick={() => {
                  setQuery("");
                  setCountryFilter("");
                  setTypeFilter("");
                  setStatusFilter("active");
                }}
              >
                {t("clearFilters")}
              </button>
            )}
          </div>
        )}
      </section>

      {history.length > 0 && (
        <section className="vreq-history-card">
          <div className="vreq-history-title">
            <History size={17} /> {t("requirementChangeHistory")}
          </div>
          {history.map((item) => (
            <div key={item._id} className="vreq-history-row">
              <span>
                {t(
                  {
                    CREATE: "reviewActionCreate",
                    CREATED: "reviewActionCreate",
                    UPDATE: "reviewActionUpdate",
                    UPDATED: "reviewActionUpdate",
                    DELETE: "reviewActionDelete",
                    DELETED: "reviewActionDelete",
                  }[String(item.action).toUpperCase()] || item.action,
                )}
              </span>
              <span>{item.current?.name || item.previous?.name || t("requirementName")}</span>
              <span>{item.adminId || t("adminRole")}</span>
              <time>{formatDate(item.createdAt, i18n.language)}</time>
            </div>
          ))}
        </section>
      )}

      {selectedVisa && (
        <div
          className="vreq-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelectedVisa(null);
          }}
        >
          <section
            className="vreq-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="vreq-detail-title"
          >
            <header className="vreq-detail-header">
              <div>
                <h2 id="vreq-detail-title">{selectedVisa.visaTypeCode}</h2>
                <p>
                  {selectedCountry?.name || selectedVisa.destination} ·{" "}
                  {t(selectedVisa.category.toLowerCase(), { defaultValue: selectedVisa.category })} {t("visaLabel")}
                </p>
              </div>
              <button
                type="button"
                className="vreq-icon-button"
                aria-label={t("closeDetails")}
                onClick={() => setSelectedVisa(null)}
              >
                <X size={19} />
              </button>
            </header>
            <div className="vreq-detail-content">
              <div className="vreq-detail-facts">
                {[
                  [t("travelPurpose"), t(selectedVisa.category.toLowerCase(), { defaultValue: selectedVisa.category })],
                  [t("visaDuration"), translateDuration(selectedVisa.validity, t)],
                  [t("allowedStay"), `${selectedVisa.allowedStayDays} ${t("days")}`],
                  [t("entryType"), t(selectedVisa.entryType.toLowerCase())],
                  [t("nationality"), selectedVisa.applicantNationality],
                  [t("status"), selectedVisa.active ? t("active") : t("archived")],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span><strong>{value || "—"}</strong>
                  </div>
                ))}
              </div>
              <div className="vreq-detail-block">
                <div className="vreq-detail-block-heading">
                  <h3>{t("configuredRequirements")}</h3>
                  <span>{selectedRequirements.length} {t("items")}</span>
                </div>
                {selectedRequirements.length ? (
                  <div className="vreq-detail-requirements">
                    {selectedRequirements
                      .sort((left, right) => (left.displayOrder || 0) - (right.displayOrder || 0))
                      .map((item) => (
                        <article key={item._id}>
                          <span className="vreq-detail-doc-icon"><FileCheck2 size={17} /></span>
                          <div>
                            <strong>{item.name}</strong>
                            <small>
                              {(item.acceptedFormats || []).join(", ").toUpperCase()} ·{" "}
                              {item.weight ?? 0} {t("points")}
                            </small>
                          </div>
                          <span className={`vreq-status ${item.required ? "is-active" : "is-optional"}`}>
                            <i /> {item.required ? t("required") : t("optional")}
                          </span>
                          <button
                            type="button"
                            className="vreq-icon-button"
                            aria-label={`${t("viewRequirementHistory")} ${item.name}`}
                            onClick={() => loadHistory(item)}
                          >
                            <History size={16} />
                          </button>
                        </article>
                      ))}
                  </div>
                ) : (
                  <p className="vreq-detail-empty">{t("noActiveRequirements")}</p>
                )}
              </div>
              <div className="vreq-detail-block">
                <h3>{t("additionalNotes")}</h3>
                <p className="vreq-notes-display">
                  {selectedVisa.additionalNotes || t("noAdditionalNotes")}
                </p>
              </div>
              <div className="vreq-detail-dates">
                <span>{t("createdDate")} {formatDate(selectedVisa.createdAt, i18n.language)}</span>
                <span>{t("updatedDate")} {formatDate(selectedVisa.updatedAt, i18n.language)}</span>
                {selectedVisa.effectiveDate && (
                  <span>{t("effective")} {formatDate(selectedVisa.effectiveDate, i18n.language)}</span>
                )}
              </div>
            </div>
            <footer className="vreq-detail-actions">
              <button
                type="button"
                className="vreq-button vreq-button-danger-outline"
                onClick={() => setDeleteTarget(selectedVisa)}
              >
                <Trash2 size={16} /> {t("delete")}
              </button>
              <button
                type="button"
                className="vreq-button vreq-button-primary"
                onClick={() => beginEdit(selectedVisa)}
              >
                <Pencil size={16} /> {t("editVisaRequirements")}
              </button>
            </footer>
          </section>
        </div>
      )}

      {deleteTarget && (
        <div
          className="vreq-modal-backdrop vreq-confirm-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setDeleteTarget(null);
          }}
        >
          <section
            className="vreq-confirm-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="vreq-confirm-title"
            aria-describedby="vreq-confirm-description"
          >
            <span className="vreq-confirm-icon"><CircleAlert size={21} /></span>
            <h2 id="vreq-confirm-title">{t("deleteVisaConfirm")}</h2>
            <p id="vreq-confirm-description">
              <strong>{deleteTarget.visaTypeCode}</strong> {t("deleteVisaExplanation")}
            </p>
            <div className="vreq-confirm-actions">
              <button
                type="button"
                className="vreq-button vreq-button-secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={saving}
              >
                {t("cancel")}
              </button>
              <button
                type="button"
                className="vreq-button vreq-button-danger"
                onClick={deleteVisa}
                disabled={saving}
              >
                {saving ? t("deleting") : t("confirmDelete")}
              </button>
            </div>
          </section>
        </div>
      )}

      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
