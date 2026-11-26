import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./pageUtils";

export default function RequirementsPage() {
  const { t } = useTranslation();
  const [countries, setCountries] = useState([]);
  const [visaTypes, setVisaTypes] = useState([]);
  const [requirements, setRequirements] = useState([]);
  const [selectedCountryId, setSelectedCountryId] = useState("");
  const [selectedVisaTypeId, setSelectedVisaTypeId] = useState("");
  const [countrySearch, setCountrySearch] = useState("");
  const [countryForm, setCountryForm] = useState({
    name: "",
    isoCode: "",
    flag: "",
    nationality: "",
  });
  const [editingCountryId, setEditingCountryId] = useState("");
  const [visaForm, setVisaForm] = useState({
    category: "Tourist",
    entryType: "Single",
    validity: "90 Days",
    allowedStayDays: 15,
    visaClass: "S",
    applicantNationality: "KHM",
    officialSourceUrl: "",
    effectiveDate: "",
  });
  const [editingVisaTypeId, setEditingVisaTypeId] = useState("");
  const [requirementForm, setRequirementForm] = useState({
    name: "",
    weight: "",
    required: true,
    conditional: false,
    source: "",
    effectiveFrom: "",
    formats: "pdf,jpg,jpeg,png",
    allowMultiple: false,
    maxFileSize: 10485760,
    displayOrder: 0,
    status: "ACTIVE",
    minPhotoWidth: 600,
    minPhotoHeight: 600,
    minPhotoSharpness: 50,
    minPhotoBrightness: 40,
    maxPhotoBrightness: 220,
    photoFaceCenterTolerance: 0.25,
    photoMinFaceHeightRatio: 0.2,
    photoManualReviewRequired: false,
  });
  const [editingRequirementId, setEditingRequirementId] = useState("");
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isLoadingVisaTypes, setIsLoadingVisaTypes] = useState(false);
  const [isLoadingRequirements, setIsLoadingRequirements] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [actionId, setActionId] = useState("");
  const [deleteVisaType, setDeleteVisaType] = useState(null);

  useEffect(() => {
    api
      .get("/countries")
      .then((response) => setCountries(response.data.countries || []))
      .catch((requestError) =>
        setError(
          requestError?.response?.data?.message || "Unable to load countries",
        ),
      );
  }, []);

  const refreshRequirements = useCallback(async () => {
    const params = selectedVisaTypeId
      ? { visaTypeId: selectedVisaTypeId }
      : selectedCountryId
        ? { countryId: selectedCountryId }
        : {};
    params.includeArchived = true;
    setIsLoadingRequirements(true);
    try {
      const response = await api.get("/requirements", { params });
      setRequirements(response.data.requirements || []);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to load requirements",
      );
    } finally {
      setIsLoadingRequirements(false);
    }
  }, [selectedCountryId, selectedVisaTypeId]);

  const refreshVisaTypes = useCallback(async () => {
    if (!selectedCountryId) {
      setVisaTypes([]);
      setSelectedVisaTypeId("");
      return;
    }
    setIsLoadingVisaTypes(true);
    try {
      const response = await api.get("/visa-types", {
        params: { countryId: selectedCountryId },
      });
      const items = response.data.visaTypes || [];
      setVisaTypes(items);
      setSelectedVisaTypeId((current) =>
        items.some((item) => item._id === current)
          ? current
          : items[0]?._id || "",
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to load visa types",
      );
    } finally {
      setIsLoadingVisaTypes(false);
    }
  }, [selectedCountryId]);

  useEffect(() => {
    refreshVisaTypes();
  }, [refreshVisaTypes]);

  useEffect(() => {
    refreshRequirements();
  }, [refreshRequirements]);

  const saveCountry = async (event) => {
    event.preventDefault();
    setError("");
    try {
      const response = editingCountryId
        ? await api.put(`/countries/${editingCountryId}`, countryForm)
        : await api.post("/countries", countryForm);
      setCountries((current) =>
        editingCountryId
          ? current.map((item) =>
              item._id === editingCountryId ? response.data.country : item,
            )
          : [...current, response.data.country],
      );
      setSelectedCountryId(response.data.country._id);
      setCountryForm({ name: "", isoCode: "", flag: "", nationality: "" });
      setEditingCountryId("");
      setNotice(editingCountryId ? "Country updated" : "Country created");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to save country",
      );
    }
  };

  const toggleCountry = async (country) => {
    try {
      const response = await api.put(`/countries/${country._id}`, {
        active: !country.active,
      });
      setCountries((current) =>
        current.map((item) =>
          item._id === country._id ? response.data.country : item,
        ),
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to update country status",
      );
    }
  };

  const saveVisaType = async (event) => {
    event.preventDefault();
    setError("");
    try {
      const payload = {
        ...visaForm,
        countryId: selectedCountryId,
        allowedStayDays: Number(visaForm.allowedStayDays),
      };
      const response = editingVisaTypeId
        ? await api.put(`/visa-types/${editingVisaTypeId}`, payload)
        : await api.post("/visa-types", payload);
      setVisaTypes((current) =>
        editingVisaTypeId
          ? current.map((item) =>
              item._id === editingVisaTypeId ? response.data.visaType : item,
            )
          : [...current, response.data.visaType],
      );
      setSelectedVisaTypeId(response.data.visaType._id);
      setEditingVisaTypeId("");
      setNotice(`Visa type saved · ${response.data.visaType.visaTypeCode}`);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to save visa type",
      );
    }
  };

  const archiveVisaType = async (visaType) => {
    setError("");
    setActionId(visaType._id);
    try {
      const response = visaType.active
        ? await api.patch(`/visa-types/${visaType._id}/archive`)
        : await api.put(`/visa-types/${visaType._id}`, { active: true });
      setVisaTypes((current) =>
        current.map((item) =>
          item._id === visaType._id
            ? { ...item, ...response.data.visaType }
            : item,
        ),
      );
      if (selectedVisaTypeId === visaType._id && visaType.active)
        setSelectedVisaTypeId("");
      setNotice(response.data.message || "Visa type updated");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to archive visa type",
      );
    } finally {
      setActionId("");
    }
  };

  const confirmDeleteVisaType = async () => {
    if (!deleteVisaType) return;
    const visaType = deleteVisaType;
    setError("");
    setActionId(visaType._id);
    try {
      const response = await api.delete(`/visa-types/${visaType._id}`);
      if (response.data.deleted) {
        setVisaTypes((current) =>
          current.filter((item) => item._id !== visaType._id),
        );
        if (selectedVisaTypeId === visaType._id) setSelectedVisaTypeId("");
      } else if (response.data.visaType) {
        setVisaTypes((current) =>
          current.map((item) =>
            item._id === visaType._id
              ? { ...item, ...response.data.visaType }
              : item,
          ),
        );
      }
      setNotice(response.data.message || "Visa type deleted");
      setDeleteVisaType(null);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to delete visa type",
      );
    } finally {
      setActionId("");
    }
  };

  const duplicateVisaType = async (visaType) => {
    setError("");
    setActionId(visaType._id);
    try {
      const response = await api.post(`/visa-types/${visaType._id}/duplicate`);
      setVisaTypes((current) => [...current, response.data.visaType]);
      setSelectedVisaTypeId(response.data.visaType._id);
      setNotice(
        `Duplicated with ${response.data.copiedRequirementCount} independent requirements`,
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to duplicate visa type",
      );
    } finally {
      setActionId("");
    }
  };

  const saveRequirement = async (event) => {
    event.preventDefault();
    const selectedVisaType = visaTypes.find(
      (item) => item._id === selectedVisaTypeId,
    );
    if (!selectedVisaType?.active) {
      setError("Select an active visa type before saving a requirement");
      return;
    }
    setError("");
    setIsSaving(true);
    try {
      const isPhoto = /photo|photograph/i.test(requirementForm.name);
      const payload = {
        ...requirementForm,
        visaTypeId: selectedVisaTypeId,
        code: requirementForm.name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "_")
          .replace(/^_|_$/g, ""),
        weight: Number(requirementForm.weight),
        acceptedFormats: isPhoto
          ? ["jpg", "jpeg", "png"]
          : requirementForm.formats
              .split(",")
              .map((format) => format.trim().toLowerCase())
              .filter(Boolean),
        ...(isPhoto
          ? {
              photoRules: {
                minWidth: Number(requirementForm.minPhotoWidth),
                minHeight: Number(requirementForm.minPhotoHeight),
                minSharpness: Number(requirementForm.minPhotoSharpness),
                minBrightness: Number(requirementForm.minPhotoBrightness),
                maxBrightness: Number(requirementForm.maxPhotoBrightness),
                faceCenterTolerance: Number(
                  requirementForm.photoFaceCenterTolerance,
                ),
                minFaceHeightRatio: Number(
                  requirementForm.photoMinFaceHeightRatio,
                ),
                manualReviewRequired:
                  requirementForm.photoManualReviewRequired,
              },
            }
          : {}),
        maxFileSize: Number(requirementForm.maxFileSize),
        displayOrder: Number(requirementForm.displayOrder),
        status: requirementForm.status,
      };
      await (editingRequirementId
        ? api.put(`/requirements/${editingRequirementId}`, payload)
        : api.post("/requirements", payload));
      await Promise.all([refreshRequirements(), refreshVisaTypes()]);
      setEditingRequirementId("");
      setRequirementForm({
        name: "",
        weight: "",
        required: true,
        conditional: false,
        source: "",
        effectiveFrom: "",
        formats: "pdf,jpg,jpeg,png",
        allowMultiple: false,
        maxFileSize: 10485760,
        displayOrder: 0,
        status: "ACTIVE",
        minPhotoWidth: 600,
        minPhotoHeight: 600,
        minPhotoSharpness: 50,
        minPhotoBrightness: 40,
        maxPhotoBrightness: 220,
        photoFaceCenterTolerance: 0.25,
        photoMinFaceHeightRatio: 0.2,
        photoManualReviewRequired: false,
      });
      setError("");
      setNotice(
        editingRequirementId ? "Requirement updated" : "Requirement added",
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to save requirement",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const deleteRequirement = async (requirement) => {
    if (!window.confirm(`Delete or archive ${requirement.name}?`)) return;
    try {
      const response = await api.delete(`/requirements/${requirement._id}`);
      await Promise.all([refreshRequirements(), refreshVisaTypes()]);
      setNotice(response.data.message || "Requirement removed");
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message || "Unable to delete requirement",
      );
    }
  };

  const loadHistory = async (requirement) => {
    try {
      const response = await api.get(
        `/requirements/${requirement._id}/history`,
      );
      setHistory(response.data.versions || []);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to load version history",
      );
    }
  };

  const editRequirement = (requirement) => {
    setEditingRequirementId(requirement._id);
    setRequirementForm({
      name: requirement.name,
      weight: requirement.weight,
      required: requirement.required,
      conditional: requirement.conditional,
      source: requirement.source || "",
      effectiveFrom: requirement.effectiveFrom
        ? requirement.effectiveFrom.slice(0, 10)
        : "",
      formats: (requirement.acceptedFormats || []).join(","),
      allowMultiple: Boolean(requirement.allowMultiple),
      maxFileSize: requirement.maxFileSize || 10485760,
      displayOrder: requirement.displayOrder || 0,
      status: requirement.status || "ACTIVE",
      minPhotoWidth: requirement.photoRules?.minWidth || 600,
      minPhotoHeight: requirement.photoRules?.minHeight || 600,
      minPhotoSharpness: requirement.photoRules?.minSharpness ?? 50,
      minPhotoBrightness: requirement.photoRules?.minBrightness ?? 40,
      maxPhotoBrightness: requirement.photoRules?.maxBrightness ?? 220,
      photoFaceCenterTolerance:
        requirement.photoRules?.faceCenterTolerance ?? 0.25,
      photoMinFaceHeightRatio:
        requirement.photoRules?.minFaceHeightRatio ?? 0.2,
      photoManualReviewRequired:
        requirement.photoRules?.manualReviewRequired === true,
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm uppercase tracking-[0.14em] text-blue-600">
            {t("requirements")}
          </p>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">
            {t("requirements")}
          </h1>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          {notice}
        </div>
      )}

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">
          Country Management
        </h2>
        <form onSubmit={saveCountry} className="grid gap-3 md:grid-cols-5">
          <input
            required
            placeholder="Country name"
            aria-label="Country name"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={countryForm.name}
            onChange={(event) =>
              setCountryForm((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
          />
          <input
            required
            maxLength="2"
            placeholder="ISO-2 (JP)"
            aria-label="ISO country code"
            className="rounded-xl border border-slate-200 px-3 py-2 uppercase"
            value={countryForm.isoCode}
            onChange={(event) =>
              setCountryForm((current) => ({
                ...current,
                isoCode: event.target.value.toUpperCase(),
              }))
            }
          />
          <input
            placeholder="Flag emoji"
            aria-label="Country flag"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={countryForm.flag}
            onChange={(event) =>
              setCountryForm((current) => ({
                ...current,
                flag: event.target.value,
              }))
            }
          />
          <input
            placeholder="Nationality code"
            aria-label="Nationality code"
            className="rounded-xl border border-slate-200 px-3 py-2 uppercase"
            value={countryForm.nationality}
            onChange={(event) =>
              setCountryForm((current) => ({
                ...current,
                nationality: event.target.value.toUpperCase(),
              }))
            }
          />
          <button className="rounded-xl bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700">
            {editingCountryId ? "Save Country" : "Add Country"}
          </button>
        </form>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <input
            value={countrySearch}
            onChange={(event) => setCountrySearch(event.target.value)}
            placeholder="Search countries"
            aria-label="Search countries"
            className="rounded-xl border border-slate-200 px-3 py-2"
          />
          {countries
            .filter((country) =>
              `${country.name} ${country.isoCode} ${country.nationality}`
                .toLowerCase()
                .includes(countrySearch.toLowerCase()),
            )
            .map((country) => (
              <div
                key={country._id}
                className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${selectedCountryId === country._id ? "border-amber-400 bg-amber-50" : "border-slate-200"}`}
              >
                <button
                  type="button"
                  onClick={() => {
                    setSelectedVisaTypeId("");
                    setSelectedCountryId(country._id);
                  }}
                >
                  {country.flag} {country.name} ({country.isoCode})
                  {country.active ? "" : " · Inactive"}
                </button>
                <button
                  type="button"
                  aria-label={`Edit ${country.name}`}
                  onClick={() => {
                    setEditingCountryId(country._id);
                    setCountryForm({
                      name: country.name,
                      isoCode: country.isoCode,
                      flag: country.flag || "",
                      nationality: country.nationality || "",
                    });
                  }}
                >
                  Edit
                </button>
                <button type="button" onClick={() => toggleCountry(country)}>
                  {country.active ? "Deactivate" : "Activate"}
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${country.name}`}
                  onClick={() => deleteCountry(country)}
                >
                  ×
                </button>
              </div>
            ))}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-900">
            Visa Type Management
          </h2>
          <select
            aria-label="Filter country"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={selectedCountryId}
            onChange={(event) => {
              setSelectedVisaTypeId("");
              setSelectedCountryId(event.target.value);
            }}
          >
            <option value="">Select country</option>
            {countries
              .filter((country) => country.active)
              .map((country) => (
                <option key={country._id} value={country._id}>
                  {country.name}
                </option>
              ))}
          </select>
        </div>
        <form onSubmit={saveVisaType} className="grid gap-3 md:grid-cols-4">
          <select
            required
            aria-label="Visa category"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.category}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                category: event.target.value,
              }))
            }
          >
            {["Tourist", "Business", "Student", "Work", "Transit"].map(
              (item) => (
                <option key={item}>{item}</option>
              ),
            )}
          </select>
          <select
            aria-label="Entry type"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.entryType}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                entryType: event.target.value,
              }))
            }
          >
            {["Single", "Double", "Multiple"].map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          <select
            aria-label="Visa validity"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.validity}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                validity: event.target.value,
              }))
            }
          >
            {["90 Days", "180 Days", "1 Year"].map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          <select
            aria-label="Allowed stay days"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.allowedStayDays}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                allowedStayDays: Number(event.target.value),
              }))
            }
          >
            {[15, 30, 90].map((item) => (
              <option key={item} value={item}>
                {item} Days Allowed Stay
              </option>
            ))}
          </select>
          <input
            aria-label="Visa class or status code"
            placeholder="Visa class/status"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.visaClass}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                visaClass: event.target.value.toUpperCase(),
              }))
            }
          />
          <input
            required
            maxLength="3"
            aria-label="Applicant nationality"
            placeholder="Applicant nationality (KHM)"
            className="rounded-xl border border-slate-200 px-3 py-2 uppercase"
            value={visaForm.applicantNationality}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                applicantNationality: event.target.value.toUpperCase(),
              }))
            }
          />
          <input
            type="url"
            aria-label="Visa official source URL"
            placeholder="Official source URL (optional)"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.officialSourceUrl}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                officialSourceUrl: event.target.value,
              }))
            }
          />
          <input
            type="date"
            aria-label="Visa effective date"
            className="rounded-xl border border-slate-200 px-3 py-2"
            value={visaForm.effectiveDate}
            onChange={(event) =>
              setVisaForm((current) => ({
                ...current,
                effectiveDate: event.target.value,
              }))
            }
          />
          <button
            disabled={!selectedCountryId}
            className="rounded-xl bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50 md:col-span-4"
          >
            {editingVisaTypeId
              ? "Save Visa Type"
              : "Create Visa Type & Generate ID"}
          </button>
        </form>
        <div className="mt-4 space-y-2">
          {isLoadingVisaTypes && (
            <p className="text-sm text-slate-500">Loading visa types…</p>
          )}
          {visaTypes.map((item) => (
            <div
              key={item._id}
              className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border p-3 transition ${selectedVisaTypeId === item._id ? "border-blue-500 bg-blue-50 ring-1 ring-blue-200" : "border-slate-200"}`}
            >
              <button
                type="button"
                className="text-left"
                onClick={() => setSelectedVisaTypeId(item._id)}
              >
                <span className="font-semibold text-slate-900">
                  {item.visaTypeCode}
                </span>
                <span className="ml-2 text-sm text-slate-500">
                  {item.destination} · {item.category} · {item.entryType} ·{" "}
                  {item.applicantNationality}
                  {item.active ? "" : " · Archived"}
                </span>
                <span className="ml-2 rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">
                  {item.requirementCount || 0} requirements
                </span>
              </button>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setEditingVisaTypeId(item._id);
                    setVisaForm({
                      category: item.category,
                      entryType: item.entryType,
                      validity: item.validity,
                      allowedStayDays: item.allowedStayDays,
                      visaClass: item.visaClass,
                      applicantNationality: item.applicantNationality,
                      officialSourceUrl: item.officialSourceUrl || "",
                      effectiveDate: item.effectiveDate?.slice(0, 10) || "",
                    });
                  }}
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={actionId === item._id}
                  onClick={() => duplicateVisaType(item)}
                >
                  {actionId === item._id ? "Working…" : "Duplicate"}
                </button>
                <button
                  type="button"
                  disabled={actionId === item._id}
                  onClick={() => archiveVisaType(item)}
                >
                  {item.active ? "Archive" : "Activate"}
                </button>
                <button
                  type="button"
                  disabled={actionId === item._id}
                  className="text-red-700 disabled:opacity-50"
                  onClick={() => setDeleteVisaType(item)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <form
        onSubmit={saveRequirement}
        className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:grid-cols-3"
      >
        <h2 className="text-lg font-semibold text-slate-900 md:col-span-3">
          {editingRequirementId ? "Edit Requirement" : "Add Requirement"} ·{" "}
          {visaTypes.find((item) => item._id === selectedVisaTypeId)
            ?.visaTypeCode || "Select a visa type above"}
        </h2>
        {selectedVisaTypeId &&
          !visaTypes.find((item) => item._id === selectedVisaTypeId)
            ?.active && (
            <p className="text-sm text-amber-700 md:col-span-3">
              Archived visa types cannot receive new requirements. Activate it
              first.
            </p>
          )}
        <input
          required
          aria-label="Requirement name"
          placeholder="Requirement name"
          className="rounded-xl border border-slate-200 px-3 py-2"
          value={requirementForm.name}
          onChange={(event) =>
            setRequirementForm((current) => ({
              ...current,
              name: event.target.value,
            }))
          }
        />
        <input
          required
          min="0.01"
          type="number"
          aria-label="Points"
          placeholder="Points"
          className="rounded-xl border border-slate-200 px-3 py-2"
          value={requirementForm.weight}
          onChange={(event) =>
            setRequirementForm((current) => ({
              ...current,
              weight: event.target.value,
            }))
          }
        />
        <input
          aria-label="Allowed formats"
          placeholder="Allowed formats, comma separated"
          className="rounded-xl border border-slate-200 px-3 py-2"
          value={requirementForm.formats}
          onChange={(event) =>
            setRequirementForm((current) => ({
              ...current,
              formats: event.target.value,
            }))
          }
        />
        <label className="text-sm text-slate-600">
          <span className="mb-1 block">Display order</span>
          <input
            required
            type="number"
            min="0"
            max="10000"
            step="1"
            aria-label="Requirement display order"
            className="w-full rounded-xl border border-slate-200 px-3 py-2"
            value={requirementForm.displayOrder}
            onChange={(event) =>
              setRequirementForm((current) => ({
                ...current,
                displayOrder: event.target.value,
              }))
            }
          />
        </label>
        <label className="text-sm text-slate-600">
          <span className="mb-1 block">Requirement status</span>
          <select
            aria-label="Requirement status"
            className="w-full rounded-xl border border-slate-200 px-3 py-2"
            value={requirementForm.status}
            onChange={(event) =>
              setRequirementForm((current) => ({
                ...current,
                status: event.target.value,
              }))
            }
          >
            <option value="ACTIVE">Enabled</option>
            <option value="ARCHIVED">Disabled</option>
          </select>
        </label>
        <input
          type="url"
          aria-label="Official requirement source"
          placeholder="Official source URL"
          className="rounded-xl border border-slate-200 px-3 py-2"
          value={requirementForm.source}
          onChange={(event) =>
            setRequirementForm((current) => ({
              ...current,
              source: event.target.value,
            }))
          }
        />
        <input
          type="date"
          aria-label="Requirement effective date"
          className="rounded-xl border border-slate-200 px-3 py-2"
          value={requirementForm.effectiveFrom}
          onChange={(event) =>
            setRequirementForm((current) => ({
              ...current,
              effectiveFrom: event.target.value,
            }))
          }
        />
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={requirementForm.required}
              onChange={(event) =>
                setRequirementForm((current) => ({
                  ...current,
                  required: event.target.checked,
                }))
              }
            />
            Required
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={requirementForm.conditional}
              onChange={(event) =>
                setRequirementForm((current) => ({
                  ...current,
                  conditional: event.target.checked,
                  required: event.target.checked ? false : current.required,
                }))
              }
            />
            Conditional
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={requirementForm.allowMultiple}
              onChange={(event) =>
                setRequirementForm((current) => ({
                  ...current,
                  allowMultiple: event.target.checked,
                }))
              }
            />
            Allow multiple files
          </label>
        </div>
        {/photo|photograph/i.test(requirementForm.name) && (
          <fieldset className="grid gap-3 rounded-xl border border-blue-100 bg-blue-50/50 p-4 md:col-span-3 md:grid-cols-3">
            <legend className="px-1 text-sm font-semibold text-slate-800">
              Applicant photo rules
            </legend>
            {[
              ["minPhotoWidth", "Minimum width (pixels)", 600, 1, 10000],
              ["minPhotoHeight", "Minimum height (pixels)", 600, 1, 10000],
              [
                "minPhotoSharpness",
                "Minimum clarity score",
                50,
                0,
                10000,
              ],
              ["minPhotoBrightness", "Minimum brightness (0-255)", 40, 0, 255],
              ["maxPhotoBrightness", "Maximum brightness (0-255)", 220, 0, 255],
              [
                "photoFaceCenterTolerance",
                "Face center tolerance (0-0.5)",
                0.25,
                0,
                0.5,
              ],
              [
                "photoMinFaceHeightRatio",
                "Minimum face height ratio (0-1)",
                0.2,
                0,
                1,
              ],
            ].map(([field, label, step, min, max]) => (
              <label key={field} className="text-sm text-slate-600">
                <span className="mb-1 block">{label}</span>
                <input
                  type="number"
                  min={min}
                  max={max}
                  step={field.includes("Ratio") || field.includes("Tolerance") ? 0.01 : 1}
                  aria-label={label}
                  className="w-full rounded-xl border border-slate-200 px-3 py-2"
                  value={requirementForm[field] ?? step}
                  onChange={(event) =>
                    setRequirementForm((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }))
                  }
                />
              </label>
            ))}
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={requirementForm.photoManualReviewRequired}
                onChange={(event) =>
                  setRequirementForm((current) => ({
                    ...current,
                    photoManualReviewRequired: event.target.checked,
                  }))
                }
              />
              Always require manual review
            </label>
            <p className="text-xs text-slate-600 md:col-span-3">
              Face detection checks photo layout only; it does not confirm identity or authenticity.
            </p>
          </fieldset>
        )}
        <button
          disabled={
            !selectedVisaTypeId ||
            !visaTypes.find((item) => item._id === selectedVisaTypeId)
              ?.active ||
            isSaving
          }
          className="rounded-xl bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50 md:col-span-3"
        >
          {isSaving
            ? "Saving..."
            : editingRequirementId
              ? "Save Requirement"
              : "Add Requirement"}
        </button>
      </form>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">
            Requirements{" "}
            {selectedVisaTypeId
              ? `for ${visaTypes.find((item) => item._id === selectedVisaTypeId)?.visaTypeCode || "selected visa type"}`
              : ""}
          </h2>
          {isLoadingRequirements && (
            <span className="text-sm text-slate-500">Refreshing...</span>
          )}
        </div>
        <div className="mb-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {requirements.map((item) => (
            <div
              key={item._id}
              className="rounded-xl border border-slate-200 p-4"
            >
              <div className="flex items-center justify-between">
                <div className="font-medium text-slate-900">{item.name}</div>
                <span className="rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700">
                  {item.required ? "Required" : "Optional"}
                </span>
              </div>
              <div className="mt-2 text-sm text-slate-600">
                Points: {item.weight || 1} ·{" "}
                {item.conditional ? "Conditional" : "Applicable"}
              </div>
              <div className="mt-1 text-sm text-slate-600">
                Source: {item.source || "Not provided"} (
                {item.sourceStatus || "UNVERIFIED"})
              </div>
              <div className="mt-1 text-sm text-slate-600">
                Effective:{" "}
                {item.effectiveFrom
                  ? new Date(item.effectiveFrom).toLocaleDateString()
                  : "Not specified"}
              </div>
              <div className="mt-1 text-sm text-slate-600">
                Format: {item.acceptedFormats?.join(", ") || "pdf, jpg"}
                {item.allowMultiple ? " · Multiple files" : " · One file"}
              </div>
              <div className="mt-1 text-sm text-slate-600">
                Order: {item.displayOrder || 0} ·{" "}
                {item.status === "ARCHIVED" ? "Disabled" : "Enabled"}
              </div>
              <div className="mt-3 flex gap-3 text-sm text-blue-700">
                <button type="button" onClick={() => editRequirement(item)}>
                  Edit
                </button>
                <button type="button" onClick={() => deleteRequirement(item)}>
                  Delete
                </button>
                <button type="button" onClick={() => loadHistory(item)}>
                  History
                </button>
              </div>
            </div>
          ))}
          {requirements.length === 0 && (
            <p className="text-sm text-slate-500">
              No requirements configured for this visa type yet.
            </p>
          )}
        </div>
        {history.length > 0 && (
          <div className="mt-4 rounded-xl bg-slate-50 p-4">
            <h3 className="font-semibold">Requirement Version History</h3>
            {history.map((version) => (
              <div key={version._id} className="mt-2 text-sm text-slate-600">
                {version.action} · {version.adminId || "Admin"} ·{" "}
                {new Date(version.createdAt).toLocaleString()}
              </div>
            ))}
          </div>
        )}
      </div>
      {deleteVisaType && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-visa-type-title"
        >
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2
              id="delete-visa-type-title"
              className="text-lg font-semibold text-slate-900"
            >
              Delete visa type?
            </h2>
            <p className="mt-3 text-sm text-slate-600">
              You are about to delete{" "}
              <strong>{deleteVisaType.visaTypeCode}</strong>
              {deleteVisaType.name ? ` — ${deleteVisaType.name}` : ""}.
              Referenced types are safely archived instead of permanently
              deleted.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                disabled={actionId === deleteVisaType._id}
                className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium"
                onClick={() => setDeleteVisaType(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={actionId === deleteVisaType._id}
                className="rounded-xl bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                onClick={confirmDeleteVisaType}
              >
                {actionId === deleteVisaType._id ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
