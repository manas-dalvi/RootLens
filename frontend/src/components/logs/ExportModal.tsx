"use client";

import React, { useState, useRef, useEffect, useId } from "react";
import { Icon } from "@/components/ui/Icon";
import { fetchLogs, type LogDocument, type LogSearchParams } from "@/lib/api/logs";
import {
  type UserColumnPreferences,
  getVisibleColumns,
} from "./columnConfig";
import {
  serializeLogsToCsv,
  serializeLogsToJson,
  generateExportFilename,
  fetchAllFilteredLogs,
  triggerBrowserDownload,
  MAX_EXPORT_LIMIT,
  type ExportScope,
  type ExportFormat,
  type ExportProgress,
  type LogDocumentLike,
} from "@/lib/exportUtils";

export interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentPageItems: LogDocument[];
  currentPage: number;
  totalEvents: number;
  searchParams: LogSearchParams;
  columnPreferences: UserColumnPreferences;
  onExportSuccess?: (count: number, format: ExportFormat, filename: string) => void;
}

function ExportModalDialog({
  onClose,
  currentPageItems,
  currentPage,
  totalEvents,
  searchParams,
  columnPreferences,
  onExportSuccess,
}: Omit<ExportModalProps, "isOpen">) {
  const [scope, setScope] = useState<ExportScope>(() =>
    currentPageItems.length === 0 && totalEvents > 0 ? "all" : "page"
  );
  const [format, setFormat] = useState<ExportFormat>("json");
  const [isExporting, setIsExporting] = useState<boolean>(false);
  const [progress, setProgress] = useState<ExportProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const descId = useId();

  // Keyboard accessibility: Escape to cancel or close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (isExporting && abortControllerRef.current) {
          abortControllerRef.current.abort();
        } else {
          onClose();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isExporting, onClose]);

  // Focus modal dialog when mounted
  useEffect(() => {
    modalRef.current?.focus();
  }, []);

  const visibleColumns = getVisibleColumns(columnPreferences);
  const exportableTotal = Math.min(totalEvents, MAX_EXPORT_LIMIT);
  const isCapped = totalEvents > MAX_EXPORT_LIMIT;

  const handleCancel = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setIsExporting(false);
  };

  const handleStartExport = async () => {
    setIsExporting(true);
    setError(null);
    setProgress(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      let logsToExport: LogDocumentLike[];

      if (scope === "page") {
        logsToExport = currentPageItems;
      } else {
        const result = await fetchAllFilteredLogs(
          searchParams,
          totalEvents,
          (params, signal) => fetchLogs(params, signal),
          (prog) => setProgress(prog),
          controller.signal
        );
        logsToExport = result.logs;
      }

      if (logsToExport.length === 0) {
        setError("No records available to export for the current criteria.");
        setIsExporting(false);
        return;
      }

      const filename = generateExportFilename({
        scope,
        format,
        service: searchParams.service,
        totalCount: logsToExport.length,
        page: currentPage,
      });

      let blob: Blob;
      if (format === "json") {
        const jsonContent = serializeLogsToJson(logsToExport);
        blob = new Blob([jsonContent], {
          type: "application/json;charset=utf-8",
        });
      } else {
        const csvContent = serializeLogsToCsv(logsToExport, columnPreferences);
        blob = new Blob([csvContent], {
          type: "text/csv;charset=utf-8",
        });
      }

      triggerBrowserDownload(blob, filename);

      onExportSuccess?.(logsToExport.length, format, filename);
      onClose();
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("Export was cancelled.");
      } else {
        setError(
          err instanceof Error
            ? err.message
            : "An unexpected error occurred during export."
        );
      }
    } finally {
      setIsExporting(false);
      abortControllerRef.current = null;
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-2xs transition-opacity"
    >
      <div
        ref={modalRef}
        tabIndex={-1}
        className="bg-white rounded-xl border border-[#D8D5CD] shadow-2xl max-w-lg w-full overflow-hidden text-[#171A18] focus:outline-none flex flex-col animate-in fade-in zoom-in-95 duration-150"
      >
        {/* Modal Header */}
        <div className="px-6 py-4 bg-[#FAF8F5] border-b border-[#D8D5CD] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#EBF2EE] border border-[#1F5A43]/20 flex items-center justify-center text-[#1F5A43]">
              <Icon name="file_download" size={18} />
            </div>
            <div>
              <h2 id={titleId} className="text-[16px] font-semibold text-[#171A18]">
                Export Log Records
              </h2>
              <p id={descId} className="text-[12px] text-[#606366]">
                Export query results for offline investigation and compliance.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={isExporting ? handleCancel : onClose}
            aria-label="Close export dialog"
            className="p-1.5 rounded-lg text-[#606366] hover:text-[#171A18] hover:bg-[#F0EDE6] transition-colors cursor-pointer"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 flex flex-col gap-5 text-[13px]">
          {/* Section 1: Scope Selection */}
          <div className="flex flex-col gap-2">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-[#606366]">
              1. Export Scope
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {/* Option A: Current Page */}
              <label
                className={`flex flex-col p-3 rounded-xl border transition-colors cursor-pointer ${
                  scope === "page"
                    ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
                    : "bg-[#FAF8F5] border-[#D8D5CD] text-[#606366] hover:border-[#1F5A43]/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-[#171A18] text-[13px]">
                    Current Page
                  </span>
                  <input
                    type="radio"
                    name="exportScope"
                    value="page"
                    checked={scope === "page"}
                    onChange={() => setScope("page")}
                    disabled={isExporting}
                    className="accent-[#1F5A43] cursor-pointer"
                  />
                </div>
                <span className="text-[11px] text-[#606366]">
                  {currentPageItems.length} records (Page {currentPage})
                </span>
              </label>

              {/* Option B: All Matching Results */}
              <label
                className={`flex flex-col p-3 rounded-xl border transition-colors cursor-pointer ${
                  scope === "all"
                    ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
                    : "bg-[#FAF8F5] border-[#D8D5CD] text-[#606366] hover:border-[#1F5A43]/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-[#171A18] text-[13px]">
                    All Matching Results
                  </span>
                  <input
                    type="radio"
                    name="exportScope"
                    value="all"
                    checked={scope === "all"}
                    onChange={() => setScope("all")}
                    disabled={isExporting || totalEvents === 0}
                    className="accent-[#1F5A43] cursor-pointer"
                  />
                </div>
                <span className="text-[11px] text-[#606366]">
                  {totalEvents.toLocaleString()} total events
                </span>
              </label>
            </div>

            {/* Boundary Notice if total exceeds 10,000 */}
            {scope === "all" && isCapped && (
              <div className="p-2.5 rounded-lg bg-[#FFF8E6] border border-[#FFE082] text-[11px] text-[#8C6B00] flex items-start gap-2">
                <Icon name="warning" size={16} className="shrink-0 mt-0.5" />
                <span>
                  Query matches {totalEvents.toLocaleString()} records. Due to
                  OpenSearch pagination limits, the export is capped at the first{" "}
                  {MAX_EXPORT_LIMIT.toLocaleString()} records.
                </span>
              </div>
            )}
          </div>

          {/* Section 2: Format Selection */}
          <div className="flex flex-col gap-2">
            <label className="text-[11px] font-semibold uppercase tracking-wider text-[#606366]">
              2. File Format
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {/* Option A: JSON */}
              <label
                className={`flex flex-col p-3 rounded-xl border transition-colors cursor-pointer ${
                  format === "json"
                    ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
                    : "bg-[#FAF8F5] border-[#D8D5CD] text-[#606366] hover:border-[#1F5A43]/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-[#171A18] text-[13px]">
                    JSON Format
                  </span>
                  <input
                    type="radio"
                    name="exportFormat"
                    value="json"
                    checked={format === "json"}
                    onChange={() => setFormat("json")}
                    disabled={isExporting}
                    className="accent-[#1F5A43] cursor-pointer"
                  />
                </div>
                <span className="text-[11px] text-[#606366]">
                  Complete schema with attributes, trace & span IDs
                </span>
              </label>

              {/* Option B: CSV */}
              <label
                className={`flex flex-col p-3 rounded-xl border transition-colors cursor-pointer ${
                  format === "csv"
                    ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
                    : "bg-[#FAF8F5] border-[#D8D5CD] text-[#606366] hover:border-[#1F5A43]/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-[#171A18] text-[13px]">
                    CSV (Excel Ready)
                  </span>
                  <input
                    type="radio"
                    name="exportFormat"
                    value="csv"
                    checked={format === "csv"}
                    onChange={() => setFormat("csv")}
                    disabled={isExporting}
                    className="accent-[#1F5A43] cursor-pointer"
                  />
                </div>
                <span className="text-[11px] text-[#606366]">
                  RFC 4180 with UTF-8 BOM ({visibleColumns.length} visible columns)
                </span>
              </label>
            </div>
          </div>

          {/* Section 3: Active Filters Context */}
          <div className="p-3 rounded-lg bg-[#FAF8F5] border border-[#D8D5CD] flex flex-col gap-1 text-[11px] text-[#606366]">
            <span className="font-medium text-[#171A18]">Export Scope Context:</span>
            <div className="flex flex-wrap items-center gap-1.5 font-mono">
              {searchParams.q && (
                <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD]">
                  Query: {searchParams.q}
                </span>
              )}
              {searchParams.service && (
                <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD]">
                  Service: {searchParams.service}
                </span>
              )}
              {searchParams.severity && (
                <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD]">
                  Severity: {searchParams.severity}
                </span>
              )}
              <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD]">
                Sort: {searchParams.sort_order === "asc" ? "Oldest First" : "Newest First"}
              </span>
            </div>
          </div>

          {/* Section 4: Progress Indicator */}
          {isExporting && progress && (
            <div className="p-3.5 rounded-xl bg-[#EBF2EE] border border-[#1F5A43]/30 flex flex-col gap-2">
              <div className="flex items-center justify-between text-[12px] font-medium text-[#1F5A43]">
                <span>
                  Fetching page {progress.currentPage} of {progress.totalPages}...
                </span>
                <span>
                  {progress.fetchedRecords.toLocaleString()} /{" "}
                  {progress.totalRecords.toLocaleString()} ({progress.percent}%)
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-white overflow-hidden border border-[#1F5A43]/20">
                <div
                  className="h-full bg-[#1F5A43] transition-all duration-150"
                  style={{ width: `${progress.percent}%` }}
                />
              </div>
              {progress.countChangedWarning && (
                <span className="text-[11px] text-[#8C6B00]">
                  Notice: Total record count changed in OpenSearch during export.
                </span>
              )}
            </div>
          )}

          {/* Section 5: Error Banner */}
          {error && (
            <div className="p-3 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] text-[12px] text-[#A34B4B] flex items-center gap-2">
              <Icon name="error" size={16} className="shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3.5 bg-[#FAF8F5] border-t border-[#D8D5CD] flex items-center justify-end gap-2.5">
          {isExporting ? (
            <button
              type="button"
              onClick={handleCancel}
              className="px-4 py-2 rounded-lg text-[13px] font-medium text-[#A34B4B] bg-white border border-[#FCD3CE] hover:bg-[#FEECEB] transition-colors cursor-pointer"
            >
              Cancel Export
            </button>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-[13px] font-medium text-[#606366] bg-white border border-[#D8D5CD] hover:bg-[#F0EDE6] transition-colors cursor-pointer"
            >
              Close
            </button>
          )}

          <button
            type="button"
            onClick={handleStartExport}
            disabled={isExporting || totalEvents === 0}
            className="px-4 py-2 rounded-lg text-[13px] font-medium text-white bg-[#1F5A43] hover:bg-[#184735] transition-colors flex items-center gap-1.5 cursor-pointer shadow-xs disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isExporting ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>Exporting...</span>
              </>
            ) : (
              <>
                <Icon name="file_download" size={16} />
                <span>
                  Export{" "}
                  {scope === "page"
                    ? `${currentPageItems.length} Records`
                    : `${exportableTotal.toLocaleString()} Records`}
                </span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ExportModal(props: ExportModalProps) {
  if (!props.isOpen) return null;
  return <ExportModalDialog {...props} />;
}
