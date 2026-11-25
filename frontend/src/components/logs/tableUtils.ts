export type StatusVariant = "critical" | "warning" | "healthy" | "info" | "neutral";

export type CanonicalColumnId =
  | "timestamp"
  | "log_level"
  | "service_name"
  | "message"
  | "host"
  | "environment"
  | "trace_id"
  | "span_id"
  | "id";

export interface LogDocumentLike {
  id: string;
  index?: string | null;
  timestamp?: string | null;
  service_name?: string | null;
  environment?: string | null;
  log_level?: string | null;
  message?: string | null;
  host?: string | null;
  trace_id?: string | null;
  span_id?: string | null;
  attributes?: Record<string, unknown>;
  raw_log?: string | null;
}

export const DEFAULT_PAGE_SIZES: readonly number[] = [25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 50;

/**
 * Format a timestamp into UTC format "YYYY-MM-DD HH:mm:ss.sss".
 * Falls back to "—" if missing or invalid.
 */
export function formatUtcTimestamp(ts?: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return "—";
    const iso = d.toISOString();
    return iso.substring(0, 10) + " " + iso.substring(11, 23);
  } catch {
    return "—";
  }
}

/**
 * Maps log level strings to StatusBadge variants.
 */
export function getLevelBadgeVariant(level?: string | null): StatusVariant {
  if (!level) return "neutral";
  const upper = level.toUpperCase();
  if (upper === "ERROR" || upper === "FATAL" || upper === "CRITICAL") return "critical";
  if (upper === "WARN" || upper === "WARNING") return "warning";
  if (upper === "INFO") return "info";
  if (upper === "DEBUG" || upper === "TRACE") return "neutral";
  return "neutral";
}

/**
 * Extracts a safe string representation for any canonical column ID.
 * Falls back to "—" if undefined, null, or empty string.
 */
export function getCellValue(columnId: CanonicalColumnId | string, log: LogDocumentLike): string {
  switch (columnId) {
    case "timestamp":
      return log.timestamp ? formatUtcTimestamp(log.timestamp) : "—";
    case "log_level":
      return log.log_level ? log.log_level.toUpperCase() : "—";
    case "service_name":
      return log.service_name || "—";
    case "message":
      return log.message || "—";
    case "host":
      return log.host || "—";
    case "environment":
      return log.environment || "—";
    case "trace_id":
      return log.trace_id || "—";
    case "span_id":
      return log.span_id || "—";
    case "id":
      return log.id || "—";
    default:
      return "—";
  }
}

/**
 * Computes 1-based start and end indices for pagination range display.
 * Handles boundary conditions and empty results safely.
 */
export function calculatePaginationRange(
  currentPage: number,
  pageSize: number,
  totalEvents: number
): { start: number; end: number } {
  if (totalEvents <= 0) {
    return { start: 0, end: 0 };
  }
  const validPage = Math.max(1, currentPage);
  const validSize = Math.max(1, pageSize);
  const start = (validPage - 1) * validSize + 1;
  if (start > totalEvents) {
    return { start: totalEvents, end: totalEvents };
  }
  const end = Math.min(validPage * validSize, totalEvents);
  return { start, end };
}

/**
 * Resolves the empty state text and action configuration based on timePreset.
 */
export function getEmptyStateConfig(timePreset?: string | null): {
  isTimeConstrained: boolean;
  title: string;
  description: string;
  iconName: string;
  showSwitchToAllTime: boolean;
} {
  const isTimeConstrained = Boolean(timePreset && timePreset !== "all");
  return {
    isTimeConstrained,
    title: isTimeConstrained
      ? "No Logs in Selected Time Range"
      : "No Log Records Found",
    description: isTimeConstrained
      ? "No matching log records were found for the selected time range. Earlier or historical log records may exist outside this window."
      : "No matching log documents were returned by OpenSearch for the current filter criteria.",
    iconName: isTimeConstrained ? "schedule" : "search_off",
    showSwitchToAllTime: isTimeConstrained,
  };
}
