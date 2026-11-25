import { API_BASE_URL, API_ENDPOINTS } from "@/config/api";
import { fetchLogs, LogDocument, ApiError } from "@/lib/api/logs";

/**
 * System and backing service readiness schema matching backend GET /api/v1/ready.
 */
export interface SystemReadiness {
  status: "ready" | "degraded" | "unhealthy" | string;
  app: string;
  version: string;
  environment: string;
  dependencies: {
    postgres?: {
      status: string;
      database?: string;
    };
    opensearch?: {
      status: string;
      cluster_name?: string;
      cluster_uuid?: string;
      version?: string;
      distribution?: string;
    };
  };
}

/**
 * Aggregated dashboard telemetry metrics derived from genuine backend responses.
 */
export interface DashboardMetrics {
  totalLogs: number;
  totalErrors: number;
  totalWarnings: number;
  recentLogs: LogDocument[];
  observedServices: string[];
  readiness: SystemReadiness | null;
  fetchedAt: Date;
}

/**
 * Single bucket in time-series histogram response.
 */
export interface TimeBucket {
  timestamp: string;
  doc_count: number;
  severities: Record<string, number>;
}

/**
 * Time-series histogram response from GET /api/v1/logs/aggregations.
 */
export interface LogTimeSeriesResponse {
  interval: string;
  start_time: string | null;
  end_time: string | null;
  total_logs: number;
  buckets: TimeBucket[];
}

/**
 * Parameters for time-series aggregation query.
 */
export interface LogTimeSeriesParams {
  interval?: string;
  start_time?: string;
  end_time?: string;
  service?: string;
  environment?: string;
}

/**
 * Concrete evidence linking a detected significant event to underlying log records.
 */
export interface EventEvidence {
  log_ids: string[];
  sample_messages: string[];
}

/**
 * Deterministically detected significant log event from GET /api/v1/logs/events.
 */
export interface SignificantEvent {
  event_id: string;
  rule_name: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | string;
  service_name: string;
  count: number;
  window_start: string;
  window_end: string;
  description: string;
  evidence: EventEvidence;
}

/**
 * Response envelope for significant events.
 */
export interface SignificantEventsResponse {
  total_events: number;
  events: SignificantEvent[];
}

/**
 * Parameters for significant event detection query.
 */
export interface SignificantEventsParams {
  start_time?: string;
  end_time?: string;
  service?: string;
  error_threshold?: number;
  window_minutes?: number;
}

/**
 * Fetch system and backing store readiness from GET /api/v1/ready.
 */
export async function fetchSystemReadiness(signal?: AbortSignal): Promise<SystemReadiness> {
  const url = `${API_BASE_URL}/ready`;

  try {
    const res = await fetch(url, {
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
      throw new ApiError(res.status, detail || `Failed to fetch system readiness (HTTP ${res.status})`, detail);
    }

    const data: SystemReadiness = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message = err instanceof Error ? err.message : "Network error connecting to readiness endpoint";
    throw new ApiError(0, message);
  }
}

/**
 * Fetch complete dashboard telemetry data using efficient parallel queries.
 */
export async function fetchDashboardMetrics(signal?: AbortSignal): Promise<DashboardMetrics> {
  const [readinessResult, totalLogsRes, totalErrorsRes, totalWarningsRes, recentLogsRes] =
    await Promise.allSettled([
      fetchSystemReadiness(signal),
      fetchLogs({ page_size: 1 }, signal),
      fetchLogs({ errors_only: true, page_size: 1 }, signal),
      fetchLogs({ severity: "WARN", page_size: 1 }, signal),
      fetchLogs({ page_size: 5, sort_order: "desc" }, signal),
    ]);

  if (totalLogsRes.status === "rejected" && recentLogsRes.status === "rejected") {
    const reason = totalLogsRes.reason;
    if (reason instanceof ApiError) {
      throw reason;
    }
    if (reason instanceof DOMException && reason.name === "AbortError") {
      throw reason;
    }
    throw new ApiError(500, "Failed to retrieve telemetry data from backend.");
  }

  const readiness = readinessResult.status === "fulfilled" ? readinessResult.value : null;
  const totalLogs = totalLogsRes.status === "fulfilled" ? totalLogsRes.value.total : 0;
  const totalErrors = totalErrorsRes.status === "fulfilled" ? totalErrorsRes.value.total : 0;
  const totalWarnings = totalWarningsRes.status === "fulfilled" ? totalWarningsRes.value.total : 0;
  const recentLogs = recentLogsRes.status === "fulfilled" ? recentLogsRes.value.items : [];

  const serviceSet = new Set<string>();
  recentLogs.forEach((item) => {
    if (item.service_name && item.service_name.trim()) {
      serviceSet.add(item.service_name.trim());
    }
  });
  const observedServices = Array.from(serviceSet);

  return {
    totalLogs,
    totalErrors,
    totalWarnings,
    recentLogs,
    observedServices,
    readiness,
    fetchedAt: new Date(),
  };
}

/**
 * Fetch time-bucketed log volume and severity distribution from GET /api/v1/logs/aggregations.
 */
export async function fetchLogTimeSeries(
  params: LogTimeSeriesParams = {},
  signal?: AbortSignal
): Promise<LogTimeSeriesResponse> {
  const url = new URL(API_ENDPOINTS.logs.aggregations);
  const searchParams = new URLSearchParams();

  if (params.interval) {
    searchParams.set("interval", params.interval.trim().toLowerCase());
  }
  if (params.start_time) {
    searchParams.set("start_time", params.start_time.trim());
  }
  if (params.end_time) {
    searchParams.set("end_time", params.end_time.trim());
  }
  if (params.service) {
    searchParams.set("service", params.service.trim());
  }
  if (params.environment) {
    searchParams.set("environment", params.environment.trim());
  }

  const requestUrl = searchParams.toString() ? `${url.toString()}?${searchParams.toString()}` : url.toString();

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
        }
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(res.status, detail || `Failed to fetch log aggregations (HTTP ${res.status})`, detail);
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
    const message = err instanceof Error ? err.message : "Network error fetching log aggregations";
    throw new ApiError(0, message);
  }
}

/**
 * Fetch deterministically detected significant events from GET /api/v1/logs/events.
 */
export async function fetchSignificantEvents(
  params: SignificantEventsParams = {},
  signal?: AbortSignal
): Promise<SignificantEventsResponse> {
  const url = new URL(API_ENDPOINTS.logs.events);
  const searchParams = new URLSearchParams();

  if (params.start_time) {
    searchParams.set("start_time", params.start_time.trim());
  }
  if (params.end_time) {
    searchParams.set("end_time", params.end_time.trim());
  }
  if (params.service) {
    searchParams.set("service", params.service.trim());
  }
  if (params.error_threshold !== undefined && params.error_threshold !== null) {
    searchParams.set("error_threshold", String(params.error_threshold));
  }
  if (params.window_minutes !== undefined && params.window_minutes !== null) {
    searchParams.set("window_minutes", String(params.window_minutes));
  }

  const requestUrl = searchParams.toString() ? `${url.toString()}?${searchParams.toString()}` : url.toString();

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
        }
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(res.status, detail || `Failed to fetch significant events (HTTP ${res.status})`, detail);
    }

    const data: SignificantEventsResponse = await res.json();
    return data;
  } catch (err: unknown) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    const message = err instanceof Error ? err.message : "Network error fetching significant events";
    throw new ApiError(0, message);
  }
}
