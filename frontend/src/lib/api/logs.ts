import { API_ENDPOINTS } from "@/config/api";

/**
 * Normalized log document schema matching backend LogDocumentResponse.
 */
export interface LogDocument {
  id: string;
  index: string | null;
  timestamp: string | null;
  service_name: string | null;
  environment: string | null;
  log_level: string | null;
  message: string | null;
  host: string | null;
  trace_id: string | null;
  span_id: string | null;
  attributes: Record<string, unknown>;
  raw_log: string | null;
}

/**
 * Paginated log search response matching backend LogSearchResponse.
 */
export interface LogSearchResponse {
  items: LogDocument[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/**
 * Parameters supported by backend GET /api/v1/logs.
 */
export interface LogSearchParams {
  q?: string;
  start_time?: string;
  end_time?: string;
  service?: string;
  severity?: string;
  level?: string;
  source?: string;
  user_id?: string;
  api?: string;
  errors_only?: boolean;
  page?: number;
  page_size?: number;
  sort_order?: "asc" | "desc";
}

/**
 * Structured API error capturing HTTP status and backend detail message.
 */
export class ApiError extends Error {
  public status: number;
  public detail?: string;
  public isServiceUnavailable: boolean;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.isServiceUnavailable = status === 503;
  }
}

/**
 * Serializes query parameters, omitting undefined/null/empty values.
 */
export function buildLogSearchUrl(params: LogSearchParams = {}): string {
  const url = new URL(API_ENDPOINTS.logs.search);
  const searchParams = new URLSearchParams();

  if (params.q !== undefined && params.q !== null && params.q.trim() !== "") {
    searchParams.set("q", params.q.trim());
  }
  if (params.start_time && params.start_time.trim() !== "") {
    searchParams.set("start_time", params.start_time.trim());
  }
  if (params.end_time && params.end_time.trim() !== "") {
    searchParams.set("end_time", params.end_time.trim());
  }
  if (params.service && params.service.trim() !== "") {
    searchParams.set("service", params.service.trim());
  }
  if (params.severity && params.severity.trim() !== "") {
    searchParams.set("severity", params.severity.trim().toUpperCase());
  }
  if (params.level && params.level.trim() !== "") {
    searchParams.set("level", params.level.trim().toUpperCase());
  }
  if (params.source && params.source.trim() !== "") {
    searchParams.set("source", params.source.trim());
  }
  if (params.user_id && params.user_id.trim() !== "") {
    searchParams.set("user_id", params.user_id.trim());
  }
  if (params.api && params.api.trim() !== "") {
    searchParams.set("api", params.api.trim());
  }
  if (params.errors_only !== undefined && params.errors_only !== null && params.errors_only) {
    searchParams.set("errors_only", "true");
  }
  if (params.page !== undefined && params.page !== null && params.page >= 1) {
    searchParams.set("page", String(params.page));
  }
  if (params.page_size !== undefined && params.page_size !== null) {
    const clampedSize = Math.max(1, Math.min(100, params.page_size));
    searchParams.set("page_size", String(clampedSize));
  }
  if (params.sort_order && (params.sort_order === "asc" || params.sort_order === "desc")) {
    searchParams.set("sort_order", params.sort_order);
  }

  const queryString = searchParams.toString();
  return queryString ? `${url.toString()}?${queryString}` : url.toString();
}

/**
 * Execute search query against GET /api/v1/logs.
 */
export async function fetchLogs(
  params: LogSearchParams = {},
  signal?: AbortSignal
): Promise<LogSearchResponse> {
  const requestUrl = buildLogSearchUrl(params);

  try {
    const res = await fetch(requestUrl, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        } else if (Array.isArray(errorJson.detail)) {
          detail = errorJson.detail
            .map((item: { msg?: string }) => item.msg || JSON.stringify(item))
            .join("; ");
        } else if (typeof errorJson.message === "string") {
          detail = errorJson.message;
        }
      } catch {
        detail = res.statusText || "Request failed";
      }

      if (res.status === 503) {
        throw new ApiError(503, "OpenSearch cluster is currently unavailable.", detail);
      }
      throw new ApiError(res.status, detail || `Failed to fetch logs (HTTP ${res.status})`, detail);
    }

    const data: LogSearchResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred connecting to backend";
    throw new ApiError(0, message);
  }
}

/**
 * Retrieve a single log document by ID against GET /api/v1/logs/{id}.
 */
export async function fetchLogById(
  id: string,
  signal?: AbortSignal
): Promise<LogDocument> {
  if (!id || !id.trim()) {
    throw new ApiError(400, "Log ID must be provided.");
  }
  const endpoint = API_ENDPOINTS.logs.byId(id.trim());

  try {
    const res = await fetch(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        }
      } catch {
        detail = res.statusText;
      }
      if (res.status === 404) {
        throw new ApiError(404, `Log document with ID '${id}' not found.`, detail);
      }
      if (res.status === 503) {
        throw new ApiError(503, "OpenSearch cluster is currently unavailable.", detail);
      }
      throw new ApiError(
        res.status,
        detail || `Failed to retrieve log document (HTTP ${res.status})`,
        detail
      );
    }

    const doc: LogDocument = await res.json();
    return doc;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred retrieving log document";
    throw new ApiError(0, message);
  }
}

/**
 * Response returned upon successful log file upload acceptance.
 */
export interface LogUploadResponse {
  job_id: string;
  status: string;
  file_name: string;
  file_size_bytes: number;
  format_type: string;
  log_source_id: string;
  service_name: string;
  environment: string;
  created_at: string;
  message: string;
}

/**
 * Progress and metadata detail schema for an upload job.
 */
export interface UploadJobDetailResponse {
  id: string;
  log_source_id: string;
  service_name: string;
  environment: string;
  file_name: string;
  file_size_bytes: number;
  status: string;
  total_lines: number;
  parsed_lines: number;
  error_lines: number;
  started_at: string | null;
  completed_at: string | null;
  error_summary: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Response returned after executing OpenSearch log indexing on an upload job.
 */
export interface LogIndexingResponse {
  job_id: string;
  status: "COMPLETED" | "PARTIALLY_FAILED" | "FAILED" | string;
  target_index: string;
  total_records: number;
  indexed_records: number;
  failed_records: number;
  quarantined_records: number;
  error_summary: string | null;
  message: string;
}

/**
 * Optional metadata options when uploading a log file.
 */
export interface UploadLogOptions {
  service_name?: string;
  environment?: string;
  source_name?: string;
  log_source_id?: string;
}

/**
 * Upload a log file via multipart/form-data to POST /api/v1/logs/upload.
 */
export async function uploadLogFile(
  file: File,
  options: UploadLogOptions = {},
  signal?: AbortSignal
): Promise<LogUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  if (options.service_name?.trim()) {
    formData.append("service_name", options.service_name.trim());
  }
  if (options.environment?.trim()) {
    formData.append("environment", options.environment.trim());
  }
  if (options.source_name?.trim()) {
    formData.append("source_name", options.source_name.trim());
  }
  if (options.log_source_id?.trim()) {
    formData.append("log_source_id", options.log_source_id.trim());
  }

  const endpoint = API_ENDPOINTS.logs.upload;
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      body: formData,
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        }
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(
        res.status,
        detail || `Failed to upload log file (HTTP ${res.status})`,
        detail
      );
    }

    const data: LogUploadResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred during log upload";
    throw new ApiError(0, message);
  }
}

/**
 * Execute OpenSearch bulk indexing on an uploaded job via POST /api/v1/logs/jobs/{job_id}/index.
 */
export async function indexLogJob(
  jobId: string,
  signal?: AbortSignal
): Promise<LogIndexingResponse> {
  if (!jobId || !jobId.trim()) {
    throw new ApiError(400, "Job ID must be provided for indexing.");
  }
  const endpoint = API_ENDPOINTS.logs.indexJob(jobId.trim());

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        }
      } catch {
        detail = res.statusText;
      }
      if (res.status === 503) {
        throw new ApiError(503, "OpenSearch cluster is currently unavailable.", detail);
      }
      throw new ApiError(
        res.status,
        detail || `Failed to index log job (HTTP ${res.status})`,
        detail
      );
    }

    const data: LogIndexingResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred during log indexing";
    throw new ApiError(0, message);
  }
}

/**
 * Retrieve current status and line metrics for an upload job via GET /api/v1/logs/jobs/{job_id}.
 */
export async function getUploadJobDetail(
  jobId: string,
  signal?: AbortSignal
): Promise<UploadJobDetailResponse> {
  if (!jobId || !jobId.trim()) {
    throw new ApiError(400, "Job ID must be provided.");
  }
  const endpoint = API_ENDPOINTS.logs.job(jobId.trim());

  try {
    const res = await fetch(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        }
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(
        res.status,
        detail || `Failed to retrieve upload job status (HTTP ${res.status})`,
        detail
      );
    }

    const data: UploadJobDetailResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred retrieving upload job";
    throw new ApiError(0, message);
  }
}

/**
 * Individual timestamped bucket within a time-series log aggregation.
 */
export interface TimeBucket {
  timestamp: string;
  doc_count: number;
  severities: Record<string, number>;
}

/**
 * Time-series log volume aggregation response from GET /api/v1/logs/aggregations.
 */
export interface LogTimeSeriesResponse {
  interval: string;
  start_time: string | null;
  end_time: string | null;
  total_logs: number;
  buckets: TimeBucket[];
}

/**
 * Parameters supported by backend GET /api/v1/logs/aggregations.
 */
export interface LogAggregationParams {
  interval?: string;
  start_time?: string;
  end_time?: string;
  service?: string;
  environment?: string;
}

/**
 * Serializes query parameters for log aggregations endpoint.
 */
export function buildLogAggregationUrl(params: LogAggregationParams = {}): string {
  const url = new URL(API_ENDPOINTS.logs.aggregations);
  const searchParams = new URLSearchParams();

  if (params.interval && params.interval.trim() !== "") {
    searchParams.set("interval", params.interval.trim().toLowerCase());
  }
  if (params.start_time && params.start_time.trim() !== "") {
    searchParams.set("start_time", params.start_time.trim());
  }
  if (params.end_time && params.end_time.trim() !== "") {
    searchParams.set("end_time", params.end_time.trim());
  }
  if (params.service && params.service.trim() !== "") {
    searchParams.set("service", params.service.trim());
  }
  if (params.environment && params.environment.trim() !== "") {
    searchParams.set("environment", params.environment.trim());
  }

  const queryString = searchParams.toString();
  return queryString ? `${url.toString()}?${queryString}` : url.toString();
}

/**
 * Execute time-bucketed aggregation query against GET /api/v1/logs/aggregations.
 */
export async function fetchLogAggregations(
  params: LogAggregationParams = {},
  signal?: AbortSignal
): Promise<LogTimeSeriesResponse> {
  const requestUrl = buildLogAggregationUrl(params);

  try {
    const res = await fetch(requestUrl, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        if (typeof errorJson.detail === "string") {
          detail = errorJson.detail;
        } else if (typeof errorJson.message === "string") {
          detail = errorJson.message;
        }
      } catch {
        detail = res.statusText || "Request failed";
      }

      if (res.status === 503) {
        throw new ApiError(503, "OpenSearch cluster is currently unavailable.", detail);
      }
      throw new ApiError(
        res.status,
        detail || `Failed to fetch log aggregations (HTTP ${res.status})`,
        detail
      );
    }

    const data: LogTimeSeriesResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred fetching log aggregations";
    throw new ApiError(0, message);
  }
}

/**
 * LogBERT Token-Level Explainability and Candidate Prediction Interfaces.
 */
export interface CandidateTemplatePrediction {
  event: string;
  token_id: number;
  probability: number;
}

export interface TokenExplanationItem {
  position: number;
  observed_event: string;
  observed_token_id: number;
  is_mismatch: boolean;
  observed_probability: number;
  observed_nll: number;
  top_candidates: CandidateTemplatePrediction[];
  contributes_to_anomaly: boolean;
}

export interface VHMEvidence {
  vhm_distance: number;
  vhm_alpha: number;
  vhm_contribution: number;
  center_norm?: number | null;
  explanation: string;
}

export interface AnomalyExplanation {
  explanation_type: "TOKEN_MISMATCH" | "EMBEDDING_DISTANCE" | "HYBRID" | "NORMAL" | string;
  dominant_factor: "MLKP_MISMATCH" | "VHM_DISTANCE" | "NONE" | string;
  is_anomaly: boolean;
  composite_score: number;
  threshold: number;
  mlkp_contribution: number;
  vhm_contribution: number;
  mismatch_count: number;
  total_evaluated_events: number;
  mismatch_positions: number[];
  token_explanations: TokenExplanationItem[];
  vhm_evidence: VHMEvidence;
  summary: string;
}

export interface LogBERTInferenceRequest {
  session_id?: string | null;
  events: string[];
}

export interface LogBERTInferenceResponse {
  session_id?: string | null;
  is_anomaly: boolean;
  composite_score: number;
  threshold: number;
  vhm_distance: number;
  mismatch_count: number;
  mismatch_ratio: number;
  mean_nll: number;
  num_events: number;
  events: string[];
  explanation?: AnomalyExplanation | null;
}

/**
 * Execute single sequence anomaly detection with LogBERT token-level explainability.
 */
export async function detectLogBERTSequenceAnomaly(
  req: LogBERTInferenceRequest,
  signal?: AbortSignal
): Promise<LogBERTInferenceResponse> {
  const requestUrl = API_ENDPOINTS.logs.anomalies.detect;
  try {
    const res = await fetch(requestUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(req),
      signal,
    });

    if (!res.ok) {
      let detail = "";
      try {
        const errorJson = await res.json();
        detail = errorJson.detail || errorJson.message || "";
      } catch {
        detail = res.statusText || "Request failed";
      }
      throw new ApiError(
        res.status,
        detail || `Sequence anomaly detection failed (HTTP ${res.status})`,
        detail
      );
    }

    return (await res.json()) as LogBERTInferenceResponse;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message =
      err instanceof Error ? err.message : "Network error occurred detecting sequence anomaly";
    throw new ApiError(0, message);
  }
}



