/**
 * Export utilities for RootLens Log Explorer (Milestone 02 - Step 2C - Phase 8).
 * Provides RFC 4180 CSV serialization with UTF-8 BOM, full-fidelity JSON serialization,
 * safe paginated log retrieval across OpenSearch limits, and deterministic filename generation.
 */

export type ExportScope = "page" | "all";
export type ExportFormat = "json" | "csv";

export type ColumnId =
  | "timestamp"
  | "log_level"
  | "service_name"
  | "message"
  | "host"
  | "environment"
  | "trace_id"
  | "span_id"
  | "id";

export const ALL_COLUMN_IDS: readonly ColumnId[] = [
  "timestamp",
  "log_level",
  "service_name",
  "message",
  "host",
  "environment",
  "trace_id",
  "span_id",
  "id",
] as const;

export interface UserColumnPreferencesLike {
  visibleColumns: readonly ColumnId[] | ColumnId[];
  columnOrder: readonly ColumnId[] | ColumnId[];
  messageMode?: "truncate" | "wrap";
}

/**
 * OpenSearch index.max_result_window safety boundary.
 */
export const MAX_EXPORT_LIMIT = 10000;

/**
 * Maximum page size supported by RootLens backend GET /api/v1/logs.
 */
export const EXPORT_BATCH_SIZE = 100;

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

export interface ExportProgress {
  fetchedRecords: number;
  totalRecords: number;
  currentPage: number;
  totalPages: number;
  percent: number;
  countChangedWarning?: boolean;
}

export interface FilenameOptions {
  scope: ExportScope;
  format: ExportFormat;
  service?: string;
  totalCount: number;
  page?: number;
  timestamp?: Date;
}

export interface FetchLogParams {
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

export interface FetchResult {
  items: LogDocumentLike[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export type FetchLogsFn = (
  params: FetchLogParams,
  signal?: AbortSignal
) => Promise<FetchResult>;

/**
 * Escapes a single value according to RFC 4180 rules.
 * If value contains quotes, commas, CRLF, or newlines, it is enclosed in double quotes
 * and any embedded quotes are doubled ("").
 */
export function escapeCsvField(val: unknown): string {
  if (val === null || val === undefined) {
    return "";
  }
  const str = typeof val === "object" ? JSON.stringify(val) : String(val);
  if (
    str.includes('"') ||
    str.includes(",") ||
    str.includes("\n") ||
    str.includes("\r")
  ) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Resolves a field value from a LogDocument for a canonical ColumnId.
 */
export function getLogColumnValue(colId: ColumnId, log: LogDocumentLike): string {
  switch (colId) {
    case "timestamp":
      return log.timestamp || "";
    case "log_level":
      return log.log_level || "";
    case "service_name":
      return log.service_name || "";
    case "message":
      return log.message || "";
    case "host":
      return log.host || "";
    case "environment":
      return log.environment || "";
    case "trace_id":
      return log.trace_id || "";
    case "span_id":
      return log.span_id || "";
    case "id":
      return log.id || "";
    default:
      return "";
  }
}

/**
 * Serializes an array of LogDocument records to RFC 4180 CSV format with UTF-8 BOM.
 * Respects the user's visible column preferences and custom column order.
 */
export function serializeLogsToCsv(
  logs: LogDocumentLike[],
  preferences?: UserColumnPreferencesLike,
  includeAttributes: boolean = false
): string {
  let visibleCols: ColumnId[];
  if (preferences && preferences.columnOrder && preferences.visibleColumns) {
    visibleCols = preferences.columnOrder.filter((id) =>
      preferences.visibleColumns.includes(id)
    );
  } else {
    visibleCols = [...ALL_COLUMN_IDS];
  }

  const headers = visibleCols.map((c) => escapeCsvField(c));
  if (includeAttributes) {
    headers.push(escapeCsvField("attributes"));
  }

  const rows: string[] = [];
  for (const log of logs) {
    const rowValues = visibleCols.map((c) =>
      escapeCsvField(getLogColumnValue(c, log))
    );
    if (includeAttributes) {
      rowValues.push(escapeCsvField(log.attributes || {}));
    }
    rows.push(rowValues.join(","));
  }

  // Prepend UTF-8 BOM (\uFEFF) and join lines with CRLF per RFC 4180
  return "\uFEFF" + [headers.join(","), ...rows].join("\r\n");
}

/**
 * Serializes an array of LogDocument records to formatted JSON preserving full fidelity.
 */
export function serializeLogsToJson(logs: LogDocumentLike[]): string {
  return JSON.stringify(logs, null, 2);
}

/**
 * Generates an informative, deterministic export filename.
 * Pattern: rootlens-logs-[service]-[scope]-[events]events-[timestamp].[format]
 */
export function generateExportFilename(options: FilenameOptions): string {
  const date = options.timestamp || new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  const dateStr = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(
    date.getDate()
  )}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;

  let servicePart = "all-services";
  if (options.service && options.service.trim()) {
    servicePart = options.service
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]/g, "_");
  }

  const scopePart =
    options.scope === "page"
      ? `p${options.page || 1}-${options.totalCount}events`
      : `all-${options.totalCount}events`;

  return `rootlens-logs-${servicePart}-${scopePart}-${dateStr}.${options.format}`;
}

/**
 * Sequentially fetches all matching records up to MAX_EXPORT_LIMIT (10,000).
 * Yields progress updates and respects cancellation via AbortSignal.
 */
export async function fetchAllFilteredLogs(
  params: FetchLogParams,
  totalExpected: number,
  fetchFn: FetchLogsFn,
  onProgress?: (progress: ExportProgress) => void,
  signal?: AbortSignal
): Promise<{ logs: LogDocumentLike[]; totalActual: number; countChanged: boolean }> {
  const maxToFetch = Math.min(Math.max(0, totalExpected), MAX_EXPORT_LIMIT);
  if (maxToFetch === 0) {
    return { logs: [], totalActual: 0, countChanged: false };
  }

  const totalPages = Math.ceil(maxToFetch / EXPORT_BATCH_SIZE);
  const accumulated: LogDocumentLike[] = [];
  let countChanged = false;

  for (let page = 1; page <= totalPages; page++) {
    if (signal?.aborted) {
      const err = new DOMException("Export was cancelled by user.", "AbortError");
      throw err;
    }

    const pageParams: FetchLogParams = {
      ...params,
      page,
      page_size: EXPORT_BATCH_SIZE,
    };

    const res = await fetchFn(pageParams, signal);

    if (res.total !== totalExpected) {
      countChanged = true;
    }

    accumulated.push(...res.items);

    const progress: ExportProgress = {
      fetchedRecords: accumulated.length,
      totalRecords: maxToFetch,
      currentPage: page,
      totalPages,
      percent: Math.min(
        100,
        Math.round((accumulated.length / maxToFetch) * 100)
      ),
      countChangedWarning: countChanged,
    };

    onProgress?.(progress);

    if (res.items.length < EXPORT_BATCH_SIZE || accumulated.length >= maxToFetch) {
      break;
    }

    // Yield control to event loop so browser stays interactive
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  return {
    logs: accumulated,
    totalActual: accumulated.length,
    countChanged,
  };
}

/**
 * Triggers a client-side file download via a transient anchor element.
 */
export function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
