"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { LogDocument } from "@/lib/api/logs";
import {
  ColumnId,
  UserColumnPreferences,
  saveStoredPreferences,
  getVisibleColumns,
} from "./columnConfig";
import { ColumnManager } from "./ColumnManager";
import { LogTablePagination } from "./LogTablePagination";
import {
  formatUtcTimestamp,
  getLevelBadgeVariant,
  getEmptyStateConfig,
} from "./tableUtils";

export interface LogTableProps {
  logs: LogDocument[];
  isLoading: boolean;
  selectedLogId: string | null;
  onSelectLog: (log: LogDocument) => void;
  preferences: UserColumnPreferences;
  onPreferencesChange: (newPrefs: UserColumnPreferences) => void;
  sortOrder: "desc" | "asc";
  onSortChange: (newOrder: "desc" | "asc") => void;
  // Pagination
  currentPage: number;
  totalPages: number;
  totalEvents: number;
  pageSize: number;
  onPageChange: (newPage: number) => void;
  onPageSizeChange: (newPageSize: number) => void;
  // Actions
  onClearFilters?: () => void;
  hasActiveFilters?: boolean;
  latencyMs?: number | null;
  className?: string;
  timePreset?: string;
  onSwitchToAllTime?: () => void;
}

export function LogTable({
  logs,
  isLoading,
  selectedLogId,
  onSelectLog,
  preferences,
  onPreferencesChange,
  sortOrder,
  onSortChange,
  currentPage,
  totalPages,
  totalEvents,
  pageSize,
  onPageChange,
  onPageSizeChange,
  onClearFilters,
  hasActiveFilters = false,
  latencyMs,
  className = "",
  timePreset,
  onSwitchToAllTime,
}: LogTableProps) {
  // Ordered visible columns based on user preferences
  const visibleColumns = useMemo(
    () => getVisibleColumns(preferences),
    [preferences]
  );

  // Toggle message wrap/truncate mode and persist
  const handleToggleMessageMode = () => {
    const nextMode = preferences.messageMode === "wrap" ? "truncate" : "wrap";
    const updated: UserColumnPreferences = {
      ...preferences,
      messageMode: nextMode,
    };
    saveStoredPreferences(updated);
    onPreferencesChange(updated);
  };

  const emptyConfig = getEmptyStateConfig(timePreset);

  // Helper to render cell content per canonical ColumnId
  const renderCell = (colId: ColumnId, log: LogDocument) => {
    switch (colId) {
      case "timestamp":
        return (
          <span
            className="font-mono text-[11px] whitespace-nowrap text-[#171A18]"
            title={log.timestamp || ""}
          >
            {formatUtcTimestamp(log.timestamp)}
          </span>
        );

      case "log_level":
        return (
          <div className="flex justify-center">
            <StatusBadge
              label={log.log_level?.toUpperCase() || "—"}
              variant={getLevelBadgeVariant(log.log_level)}
            />
          </div>
        );

      case "service_name": {
        const levelVariant = getLevelBadgeVariant(log.log_level);
        const dotBg =
          levelVariant === "critical"
            ? "bg-[#E63316]"
            : levelVariant === "warning"
            ? "bg-[#B77900]"
            : "bg-[#1F5A43]";
        return (
          <div
            className="flex items-center gap-1.5 min-w-0"
            title={log.service_name || ""}
          >
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotBg}`} />
            <span className="font-mono text-[11px] font-medium text-[#171A18] truncate">
              {log.service_name || "—"}
            </span>
          </div>
        );
      }

      case "message":
        return (
          <div
            title={log.message || ""}
            className={
              preferences.messageMode === "wrap"
                ? "break-words whitespace-pre-wrap font-mono text-[11px] text-[#171A18] leading-relaxed py-0.5"
                : "truncate whitespace-nowrap overflow-hidden text-ellipsis font-mono text-[11px] text-[#171A18] max-w-xl"
            }
          >
            {log.message || "—"}
          </div>
        );

      case "host":
        return (
          <span
            className="font-mono text-[11px] text-[#606366] truncate block max-w-[140px]"
            title={log.host || ""}
          >
            {log.host || "—"}
          </span>
        );

      case "environment":
        return log.environment ? (
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#F0EDE6] text-[#171A18] border border-[#D8D5CD] truncate max-w-[110px] inline-block"
            title={log.environment}
          >
            {log.environment}
          </span>
        ) : (
          <span className="font-mono text-[11px] text-[#8A8780]">—</span>
        );

      case "trace_id":
        return (
          <span
            className="font-mono text-[11px] text-[#606366] truncate block max-w-[130px]"
            title={log.trace_id || ""}
          >
            {log.trace_id || "—"}
          </span>
        );

      case "span_id":
        return (
          <span
            className="font-mono text-[11px] text-[#606366] truncate block max-w-[100px]"
            title={log.span_id || ""}
          >
            {log.span_id || "—"}
          </span>
        );

      case "id":
        return (
          <span
            className="font-mono text-[11px] text-[#8A8780] truncate block max-w-[110px]"
            title={log.id}
          >
            {log.id || "—"}
          </span>
        );

      default:
        return "—";
    }
  };

  return (
    <div
      className={`bg-white rounded-xl border border-[#D8D5CD] shadow-xs overflow-hidden flex flex-col w-full text-[#171A18] ${className}`}
    >
      {/* Top Table Control Toolbar */}
      <div className="px-4 py-2.5 bg-[#FAF8F5] border-b border-[#D8D5CD] flex flex-wrap items-center justify-between gap-3 text-[12px]">
        {/* Left: Summary and latency metadata */}
        <div className="flex items-center gap-2 text-[#606366]">
          <span className="font-semibold text-[#171A18]">Log Records</span>
          <span className="font-mono text-[11px] text-[#8A8780]">
            ({totalEvents.toLocaleString()} total)
          </span>
          {latencyMs !== null && latencyMs !== undefined && (
            <span className="inline-flex items-center gap-1 font-mono text-[11px] text-[#8A8780] bg-[#F0EDE6] px-2 py-0.5 rounded border border-[#D8D5CD]">
              <Icon name="speed" size={13} />
              <span>{latencyMs}ms</span>
            </span>
          )}
        </div>

        {/* Right: Message wrap toggle and Column Manager */}
        <div className="flex items-center gap-2">
          {/* Wrap / Truncate Toggle Button */}
          <button
            type="button"
            onClick={handleToggleMessageMode}
            className={`px-2.5 py-1.5 rounded-lg border text-[12px] font-medium transition-colors flex items-center gap-1.5 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43] ${
              preferences.messageMode === "wrap"
                ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
                : "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F5F3EE]"
            }`}
            title={
              preferences.messageMode === "wrap"
                ? "Switch to single-line truncation"
                : "Switch to multi-line message wrapping"
            }
            aria-label="Toggle log message wrap mode"
          >
            <Icon name="wrap_text" size={15} />
            <span>{preferences.messageMode === "wrap" ? "Wrap" : "Truncate"}</span>
          </button>

          {/* Column Manager Popover */}
          <ColumnManager
            preferences={preferences}
            onPreferencesChange={onPreferencesChange}
          />
        </div>
      </div>

      {/* Semantic Table Container with Horizontal Scroll Support */}
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left border-collapse text-[12px]">
          {/* Table Header */}
          <thead>
            <tr className="bg-[#F0EDE6] border-b border-[#D8D5CD] text-[11px] font-semibold text-[#171A18] uppercase tracking-wider">
              {visibleColumns.map((col) => {
                if (col.id === "timestamp") {
                  return (
                    <th
                      key={col.id}
                      scope="col"
                      className="px-4 py-2.5 select-none"
                      style={{ minWidth: col.minWidth }}
                      aria-sort={sortOrder === "desc" ? "descending" : "ascending"}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          onSortChange(sortOrder === "desc" ? "asc" : "desc")
                        }
                        className="flex items-center gap-1 hover:text-[#1F5A43] transition-colors cursor-pointer group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43] rounded px-1 -ml-1"
                        title={`Sorted by timestamp ${
                          sortOrder === "desc" ? "newest first" : "oldest first"
                        } (click to reverse)`}
                      >
                        <span>Time (UTC)</span>
                        <Icon
                          name={
                            sortOrder === "desc"
                              ? "arrow_downward"
                              : "arrow_upward"
                          }
                          size={14}
                          className="text-[#1F5A43] transition-transform"
                        />
                      </button>
                    </th>
                  );
                }

                if (col.id === "log_level") {
                  return (
                    <th
                      key={col.id}
                      scope="col"
                      className="px-3 py-2.5 text-center"
                      style={{ minWidth: col.minWidth }}
                    >
                      {col.label}
                    </th>
                  );
                }

                return (
                  <th
                    key={col.id}
                    scope="col"
                    className="px-3 py-2.5"
                    style={{ minWidth: col.minWidth }}
                  >
                    {col.label}
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Table Body */}
          <tbody className="divide-y divide-[#D8D5CD] text-[12px]">
            {/* Loading State */}
            {isLoading && (
              <tr>
                <td
                  colSpan={Math.max(1, visibleColumns.length)}
                  className="py-16 text-center"
                >
                  <div className="flex flex-col items-center justify-center gap-3">
                    <div className="w-8 h-8 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
                    <span className="text-[13px] text-[#606366] font-medium">
                      Querying OpenSearch cluster...
                    </span>
                  </div>
                </td>
              </tr>
            )}

            {/* Empty State */}
            {!isLoading && logs.length === 0 && (
              <tr>
                <td
                  colSpan={Math.max(1, visibleColumns.length)}
                  className="py-16 px-6 text-center"
                >
                  <div className="flex flex-col items-center justify-center gap-3 max-w-sm mx-auto">
                    <div className="w-12 h-12 rounded-xl bg-[#EBF2EE] border border-[#1F5A43]/20 flex items-center justify-center text-[#1F5A43]">
                      <Icon name={emptyConfig.iconName} size={24} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <h3 className="text-[15px] font-semibold text-[#171A18]">
                        {emptyConfig.title}
                      </h3>
                      <p className="text-[12px] text-[#606366] leading-relaxed">
                        {emptyConfig.description}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-center gap-2 pt-2">
                      {emptyConfig.showSwitchToAllTime && onSwitchToAllTime && (
                        <button
                          type="button"
                          onClick={onSwitchToAllTime}
                          className="px-4 py-2 rounded-lg text-[13px] font-medium text-white bg-[#1F5A43] hover:bg-[#184735] transition-colors flex items-center gap-1.5 cursor-pointer shadow-xs"
                        >
                          <Icon name="schedule" size={16} />
                          <span>Switch to All Time</span>
                        </button>
                      )}
                      <Link
                        href="/logs/upload"
                        className={`px-3.5 py-2 rounded-lg text-[13px] font-medium transition-colors flex items-center gap-1.5 cursor-pointer ${
                          emptyConfig.showSwitchToAllTime
                            ? "text-[#606366] bg-white border border-[#D8D5CD] hover:bg-[#F5F3EE]"
                            : "text-white bg-[#1F5A43] hover:bg-[#184735]"
                        }`}
                      >
                        <Icon name="upload_file" size={16} />
                        <span>Upload Logs</span>
                      </Link>
                      {hasActiveFilters && onClearFilters && (
                        <button
                          type="button"
                          onClick={onClearFilters}
                          className="px-3.5 py-2 rounded-lg text-[13px] font-medium text-[#606366] bg-white border border-[#D8D5CD] hover:bg-[#F5F3EE] transition-colors cursor-pointer"
                        >
                          Clear Filters
                        </button>
                      )}
                    </div>
                  </div>
                </td>
              </tr>
            )}

            {/* Data Rows */}
            {!isLoading &&
              logs.length > 0 &&
              logs.map((log) => {
                const isSelected = selectedLogId === log.id;

                return (
                  <tr
                    key={log.id}
                    tabIndex={0}
                    role="row"
                    aria-selected={isSelected}
                    onClick={() => onSelectLog(log)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectLog(log);
                      }
                    }}
                    className={`cursor-pointer transition-colors border-l-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#1F5A43] focus-visible:bg-[#EBF2EE] ${
                      isSelected
                        ? "bg-[#EBF2EE] border-l-[#1F5A43] text-[#171A18] font-medium"
                        : "border-l-transparent hover:bg-[#FAF8F5] text-[#171A18]"
                    }`}
                  >
                    {visibleColumns.map((col) => (
                      <td
                        key={col.id}
                        className={`py-2.5 ${
                          col.id === "timestamp"
                            ? "px-4"
                            : col.id === "log_level"
                            ? "px-3 text-center"
                            : "px-3"
                        }`}
                      >
                        {renderCell(col.id, log)}
                      </td>
                    ))}
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      {!isLoading && totalEvents > 0 && (
        <LogTablePagination
          currentPage={currentPage}
          totalPages={totalPages}
          totalEvents={totalEvents}
          pageSize={pageSize}
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
          disabled={isLoading}
        />
      )}
    </div>
  );
}
