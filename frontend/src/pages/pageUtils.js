import { useEffect, useRef, useState } from "react";
import axios from "axios";

const businessApiBase = import.meta.env.VITE_API_BASE_URL || "/api";
const aiApiBase =
  import.meta.env.VITE_AI_API_BASE_URL || "http://127.0.0.1:8002";

export const BANK_STATEMENT_MAX_FILE_SIZE = 50 * 1024 * 1024;

export function isBankStatementRequirement(requirement = {}) {
  return [requirement.code, requirement.requirementCode, requirement.name]
    .filter(Boolean)
    .some((value) =>
      String(value)
        .toLowerCase()
        .replace(/[\s-]+/g, "_")
        .includes("bank_statement"),
    );
}

export function getRequirementMaxFileSize(
  requirement,
  hardLimit = Number.POSITIVE_INFINITY,
) {
  if (isBankStatementRequirement(requirement)) {
    return BANK_STATEMENT_MAX_FILE_SIZE;
  }
  return Math.min(
    Number(requirement?.maxFileSize) || 10 * 1024 * 1024,
    hardLimit,
  );
}

function normalizeBaseUrl(baseUrl, includeApiPrefix) {
  const normalized = baseUrl.replace(/\/+$/, "");
  if (!includeApiPrefix || normalized.endsWith("/api")) {
    return normalized;
  }
  return `${normalized}/api`;
}

export const getStoredAuthToken = () => {
  const token =
    localStorage.getItem("visadoc_token") ||
    localStorage.getItem("access_token") ||
    sessionStorage.getItem("visadoc_token") ||
    sessionStorage.getItem("access_token");
  return token || "";
};

function createApiClient(baseURL) {
  const client = axios.create({ baseURL, timeout: 25000 });

  client.interceptors.request.use((config) => {
    const isPublicAuthRequest = /\/auth\/(login|register)\/?$/.test(
      config.url || "",
    );
    const token = isPublicAuthRequest ? "" : getStoredAuthToken();
    if (token) {
      config.headers = {
        ...config.headers,
        Authorization: `Bearer ${token}`,
      };
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (!error.response) {
        error.message = `Network error: unable to reach ${baseURL}.`;
      } else {
        const requestUrl = error.config?.url || "";
        const isPublicAuthRequest = /\/auth\/(login|register)\/?$/.test(
          requestUrl,
        );
        const status = error.response.status;
        const message = error.response.data?.message;
        const tokenExpired =
          status === 401 ||
          (status === 403 && message === "Invalid or expired token");

        if (!isPublicAuthRequest && tokenExpired) {
          localStorage.removeItem("visadoc_token");
          localStorage.removeItem("access_token");
          sessionStorage.removeItem("visadoc_token");
          sessionStorage.removeItem("access_token");
          window.dispatchEvent(new Event("visadoc:auth-expired"));
        }
      }
      return Promise.reject(error);
    },
  );

  return client;
}

export const api = createApiClient(normalizeBaseUrl(businessApiBase, true));
export const aiApi = createApiClient(normalizeBaseUrl(aiApiBase, false));

export async function waitForDocumentOcr(
  apiClient,
  documentId,
  onProgress,
  timeoutMs = 900_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await apiClient.get(`/documents/${documentId}/processing`);
    const progress = response.data;
    onProgress?.(progress);
    const status = String(progress.ocrStatus || "").toUpperCase();
    if (status === "OCR_COMPLETED" || status === "NOT_REQUIRED") return progress;
    if (status === "OCR_PARTIAL") {
      throw new Error(
        progress.message ||
          "Some pages could not be read. Retry OCR before verification.",
      );
    }
    if (status === "OCR_FAILED") {
      throw new Error(
        progress.error ||
          progress.message ||
          "Document reading failed. Please retry.",
      );
    }
    await new Promise((resolve) => window.setTimeout(resolve, 750));
  }
  throw new Error("Document reading is taking longer than expected.");
}

export function formatNumber(value) {
  return Number(value ?? 0).toFixed(1);
}

export function useSecureData(fetcher, defaultValue = []) {
  const [data, setData] = useState(defaultValue);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    fetcherRef
      .current()
      .then((result) => {
        setData(result);
        setError("");
      })
      .catch((err) => {
        setError(err?.response?.data?.message || "Unable to load data");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  return { data, loading, error };
}
