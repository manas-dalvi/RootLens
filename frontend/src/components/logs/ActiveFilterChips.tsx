"use client";

import React from "react";
import { Icon } from "@/components/ui/Icon";

export interface ActiveFilterChipsProps {
  query?: string;
  timePreset: string;
  timePresetLabel: string;
  customStartTime?: string;
  customEndTime?: string;
  selectedBucket?: { start: string; end: string; count: number } | null;
  service?: string;
  severity?: string;
  errorsOnly?: boolean;
  userId?: string;
  api?: string;
  source?: string;
  onRemoveQuery?: () => void;
  onResetTimeRange: () => void;
  onClearBucket?: () => void;
  onRemoveService: () => void;
  onRemoveSeverity: () => void;
  onRemoveErrorsOnly: () => void;
  onRemoveUserId: () => void;
  onRemoveApi: () => void;
  onRemoveSource: () => void;
  onResetAll: () => void;
}

export function ActiveFilterChips({
  query,
  timePreset,
  timePresetLabel,
  customStartTime,
  customEndTime,
  selectedBucket,
  service,
  severity,
  errorsOnly,
  userId,
  api,
  source,
  onRemoveQuery,
  onResetTimeRange,
  onClearBucket,
  onRemoveService,
  onRemoveSeverity,
  onRemoveErrorsOnly,
  onRemoveUserId,
  onRemoveApi,
  onRemoveSource,
  onResetAll,
}: ActiveFilterChipsProps) {
  const chips: Array<{
    id: string;
    label: string;
    value: string;
    onRemove: () => void;
    variant?: "default" | "error" | "warning";
  }> = [];

  if (query && query.trim()) {
    chips.push({
      id: "query",
      label: "Query",
      value: `"${query.trim()}"`,
      onRemove: () => onRemoveQuery?.(),
    });
  }

  // Time preset chip: if custom or non-default (default is 24h)
  if (timePreset === "custom" && customStartTime && customEndTime) {
    chips.push({
      id: "time",
      label: "Time Range",
      value: `${customStartTime.replace("T", " ")} → ${customEndTime.replace("T", " ")}`,
      onRemove: onResetTimeRange,
    });
  } else if (timePreset !== "24h") {
    chips.push({
      id: "time",
      label: "Time",
      value: timePresetLabel,
      onRemove: onResetTimeRange,
    });
  }

  // Selected Histogram bucket chip
  if (selectedBucket) {
    const s = selectedBucket.start.replace("T", " ").substring(11, 16);
    const e = selectedBucket.end.replace("T", " ").substring(11, 16);
    chips.push({
      id: "bucket",
      label: "Histogram Window",
      value: `${s} → ${e} UTC (${selectedBucket.count} logs)`,
      onRemove: () => onClearBucket?.(),
    });
  }

  if (service && service.trim()) {
    chips.push({
      id: "service",
      label: "Service",
      value: service.trim(),
      onRemove: onRemoveService,
    });
  }

  if (severity && severity.trim()) {
    const isErr = severity.toUpperCase() === "ERROR" || severity.toUpperCase() === "FATAL" || severity.toUpperCase() === "CRITICAL";
    const isWarn = severity.toUpperCase() === "WARN" || severity.toUpperCase() === "WARNING";
    chips.push({
      id: "severity",
      label: "Severity",
      value: severity.trim().toUpperCase(),
      onRemove: onRemoveSeverity,
      variant: isErr ? "error" : isWarn ? "warning" : "default",
    });
  }

  if (errorsOnly) {
    chips.push({
      id: "errors_only",
      label: "Errors Only",
      value: "ON",
      onRemove: onRemoveErrorsOnly,
      variant: "error",
    });
  }

  if (userId && userId.trim()) {
    chips.push({
      id: "user_id",
      label: "User ID",
      value: userId.trim(),
      onRemove: onRemoveUserId,
    });
  }

  if (api && api.trim()) {
    chips.push({
      id: "api",
      label: "API",
      value: api.trim(),
      onRemove: onRemoveApi,
    });
  }

  if (source && source.trim()) {
    chips.push({
      id: "source",
      label: "Source",
      value: source.trim().toLowerCase(),
      onRemove: onRemoveSource,
    });
  }

  if (chips.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px]">
      <span className="text-[#5F6360] font-medium mr-1 select-none">Active Filters:</span>

      {chips.map((chip) => {
        let chipBg = "bg-white border-[#D8D5CD] text-[#171A18]";
        let badgeColor = "text-[#5F6360]";

        if (chip.variant === "error") {
          chipBg = "bg-[#FEECEB] border-[#FCD3CE] text-[#A34B4B]";
          badgeColor = "text-[#A34B4B]";
        } else if (chip.variant === "warning") {
          chipBg = "bg-[#FEF7E6] border-[#FDE8B3] text-[#9A7440]";
          badgeColor = "text-[#9A7440]";
        }

        return (
          <div
            key={chip.id}
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md border text-[11px] shadow-2xs transition-colors ${chipBg}`}
          >
            <span className={`font-medium ${badgeColor}`}>{chip.label}:</span>
            <span className="font-mono font-semibold max-w-[200px] truncate" title={chip.value}>
              {chip.value}
            </span>
            <button
              type="button"
              onClick={chip.onRemove}
              className="text-[#9E9B90] hover:text-[#171A18] ml-0.5 p-0.5 rounded-sm transition-colors cursor-pointer"
              title={`Remove ${chip.label} filter`}
              aria-label={`Remove ${chip.label} filter`}
            >
              <Icon name="close" size={12} />
            </button>
          </div>
        );
      })}

      <button
        type="button"
        onClick={onResetAll}
        className="px-2 py-1 text-[11px] font-medium text-[#5F6360] hover:text-[#171A18] hover:bg-[#EAE7DF] rounded-md transition-colors ml-1 cursor-pointer flex items-center gap-1"
        title="Reset all filters to defaults"
      >
        <Icon name="refresh" size={12} />
        <span>Reset All</span>
      </button>
    </div>
  );
}

