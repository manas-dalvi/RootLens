/**
 * Centralized API configuration for RootLens.
 * Reads NEXT_PUBLIC_API_URL from environment with fallback to default backend URL.
 */

const rawApiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// Remove any trailing slash for consistent endpoint concatenation
export const API_BASE_URL = rawApiUrl.replace(/\/+$/, "");

export const API_ENDPOINTS = {
  health: `${API_BASE_URL.replace(/\/api\/v1$/, "")}/health`,
  logs: {
    base: `${API_BASE_URL}/logs`,
    search: `${API_BASE_URL}/logs`,
    byId: (id: string) => `${API_BASE_URL}/logs/${encodeURIComponent(id)}`,
    upload: `${API_BASE_URL}/logs/upload`,
    job: (jobId: string) => `${API_BASE_URL}/logs/jobs/${encodeURIComponent(jobId)}`,
    parseJob: (jobId: string) => `${API_BASE_URL}/logs/jobs/${encodeURIComponent(jobId)}/parse`,
    normalizeJob: (jobId: string) => `${API_BASE_URL}/logs/jobs/${encodeURIComponent(jobId)}/normalize`,
    indexJob: (jobId: string) => `${API_BASE_URL}/logs/jobs/${encodeURIComponent(jobId)}/index`,
    aggregations: `${API_BASE_URL}/logs/aggregations`,
    events: `${API_BASE_URL}/logs/events`,
    anomalies: {
      detect: `${API_BASE_URL}/logs/anomalies/detect`,
      batchDetect: `${API_BASE_URL}/logs/anomalies/batch-detect`,
      modelInfo: `${API_BASE_URL}/logs/anomalies/model-info`,
    },
  },
  incidents: {
    base: `${API_BASE_URL}/incidents`,
    byId: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}`,
    status: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/status`,
    rca: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/rca`,
    candidates: `${API_BASE_URL}/incidents/candidates`,
    reports: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/reports`,
    reportById: (id: string, reportId: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/reports/${encodeURIComponent(reportId)}`,
    recommendations: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/recommendations`,
    recommendationById: (id: string, recId: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/recommendations/${encodeURIComponent(recId)}`,
    explanation: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/explanation`,
    investigate: (id: string) => `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/investigate`,
    hypothesisDecisions: (id: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/hypotheses/decisions`,
    hypothesisDecision: (id: string, hypId: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/hypotheses/${encodeURIComponent(hypId)}/decision`,
    hypothesisConfirm: (id: string, hypId: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/hypotheses/${encodeURIComponent(hypId)}/confirm`,
    hypothesisReject: (id: string, hypId: string) =>
      `${API_BASE_URL}/incidents/${encodeURIComponent(id)}/hypotheses/${encodeURIComponent(hypId)}/reject`,
  },
} as const;
