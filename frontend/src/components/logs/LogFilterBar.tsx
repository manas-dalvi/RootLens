"use client";

import React, { useState } from "react";
import { Icon } from "@/components/ui/Icon";

export type TimePreset = "15m" | "1h" | "24h" | "7d" | "all" | "custom";

export const TIME_PRESET_LABELS: Record<TimePreset, string> = {
  "24h": "Last 24h (Default)",
  "15m": "Last 15m",
  "1h": "Last 1h",
  "7d": "Last 7d",
  all: "All Time (Full Cluster)",
  custom: "Custom Range...",
};

export interface LogFilterBarProps {
  timePreset: TimePreset;
  onTimePresetChange: (preset: TimePreset) => void;
  customStartTime: string;
  customEndTime: string;
  onCustomRangeApply: (start: string, end: string) => void;
  customRangeError: string | null;
  serviceFilter: string;
  onServiceChange: (service: string) => void;
  severityFilter: string;
  onSeverityChange: (severity: string) => void;
  errorsOnly: boolean;
  onErrorsOnlyChange: (errorsOnly: boolean) => void;
  userIdFilter: string;
  onUserIdChange: (userId: string) => void;
  apiFilter: string;
  onApiChange: (api: string) => void;
  sourceFilter: string;
  onSourceChange: (source: string) => void;
  sortOrder: "desc" | "asc";
  onSortOrderChange: (order: "desc" | "asc") => void;
}

export function LogFilterBar({
  timePreset,
  onTimePresetChange,
  customStartTime,
  customEndTime,
  onCustomRangeApply,
  customRangeError,
  serviceFilter,
  onServiceChange,
  severityFilter,
  onSeverityChange,
  errorsOnly,
  onErrorsOnlyChange,
  userIdFilter,
  onUserIdChange,
  apiFilter,
  onApiChange,
  sourceFilter,
  onSourceChange,
  sortOrder,
  onSortOrderChange,
}: LogFilterBarProps) {
  const [showAdvanced, setShowAdvanced] = useState<boolean>(
    Boolean(userIdFilter || apiFilter || sourceFilter)
  );

  // Local state for custom range inputs before applying
  const [localStart, setLocalStart] = useState<string>(customStartTime || "");
  const [localEnd, setLocalEnd] = useState<string>(customEndTime || "");
  const [localError, setLocalError] = useState<string | null>(null);

  const handleApplyCustomRange = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!localStart || !localEnd) {
      setLocalError("Both start time and end time are required.");
      return;
    }

    const s = new Date(localStart).getTime();
    const end = new Date(localEnd).getTime();

    if (isNaN(s) || isNaN(end)) {
      setLocalError("Invalid date format provided.");
      return;
    }

    if (s >= end) {
      setLocalError("Start time must be strictly earlier than end time.");
      return;
    }

    setLocalError(null);
    onCustomRangeApply(localStart, localEnd);
  };

  const hasAdvancedFilters = Boolean(userIdFilter || apiFilter || sourceFilter);

  return (
    <div className="flex flex-col gap-2.5 w-full text-[12px]">
      {/* Primary Filter Row */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Time Preset Dropdown */}
        <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1.5 text-[#5F6360]">
          <Icon name="schedule" size={15} className="text-[#9E9B90]" />
          <span className="font-medium text-[#5F6360]">Time:</span>
          <select
            value={timePreset}
            onChange={(e) => {
              const val = e.target.value as TimePreset;
              onTimePresetChange(val);
              if (val === "custom") {
                // Populate default custom inputs with last 24h if empty
                if (!localStart) {
                  const now = new Date();
                  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
                  setLocalStart(yesterday.toISOString().slice(0, 16));
                  setLocalEnd(now.toISOString().slice(0, 16));
                }
              }
            }}
            className="bg-transparent text-[11px] font-mono font-medium text-[#171A18] border-0 outline-none focus:ring-0 p-0 cursor-pointer"
          >
            {(Object.keys(TIME_PRESET_LABELS) as TimePreset[]).map((key) => (
              <option key={key} value={key}>
                {TIME_PRESET_LABELS[key]}
              </option>
            ))}
          </select>
        </div>

        {/* Errors Only Quick Toggle */}
        <button
          type="button"
          onClick={() => onErrorsOnlyChange(!errorsOnly)}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-medium transition-colors cursor-pointer ${
            errorsOnly
              ? "bg-[#FEECEB] border-[#FCD3CE] text-[#A34B4B] font-semibold shadow-2xs"
              : "bg-[#F5F3EE] border-[#D8D5CD] text-[#5F6360] hover:text-[#171A18] hover:border-[#9E9B90]"
          }`}
          title="Filter only ERROR, FATAL, and CRITICAL log events"
        >
          <span
            className={`w-2 h-2 rounded-full ${errorsOnly ? "bg-[#A34B4B]" : "bg-[#9E9B90]"}`}
          />
          <span>Errors Only</span>
        </button>

        {/* Severity / Level Selector */}
        <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1.5 text-[#5F6360]">
          <Icon name="layers" size={15} className="text-[#9E9B90]" />
          <span className="font-medium text-[#5F6360]">Severity:</span>
          <select
            value={severityFilter}
            onChange={(e) => onSeverityChange(e.target.value)}
            className="bg-transparent text-[11px] font-mono font-medium text-[#171A18] border-0 outline-none focus:ring-0 p-0 cursor-pointer"
          >
            <option value="">All Severities</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="FATAL">FATAL</option>
            <option value="ERROR">ERROR</option>
            <option value="WARN">WARN</option>
            <option value="INFO">INFO</option>
            <option value="DEBUG">DEBUG</option>
          </select>
        </div>

        {/* Service Filter */}
        <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1.5 text-[#5F6360]">
          <Icon name="dns" size={15} className="text-[#9E9B90]" />
          <span className="font-medium text-[#5F6360]">Service:</span>
          <input
            type="text"
            value={serviceFilter}
            onChange={(e) => onServiceChange(e.target.value)}
            placeholder="All Services"
            className="bg-transparent text-[11px] font-mono text-[#171A18] border-0 outline-none focus:ring-0 p-0 w-24 sm:w-32 placeholder:text-[#9E9B90]"
          />
          {serviceFilter && (
            <button
              type="button"
              onClick={() => onServiceChange("")}
              className="text-[#9E9B90] hover:text-[#171A18] ml-0.5 cursor-pointer p-0.5"
              title="Clear service filter"
              aria-label="Clear service filter"
            >
              <Icon name="close" size={12} />
            </button>
          )}
        </div>

        {/* Sort Order Toggle */}
        <button
          type="button"
          onClick={() => onSortOrderChange(sortOrder === "desc" ? "asc" : "desc")}
          className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1.5 text-[#5F6360] hover:text-[#171A18] cursor-pointer"
          title={`Sort order: currently ${sortOrder.toUpperCase()} (Click to toggle)`}
        >
          <span className="font-medium">Sort:</span>
          <span className="font-mono text-[11px] font-semibold text-[#171A18]">
            {sortOrder.toUpperCase()}
          </span>
          <Icon name={sortOrder === "desc" ? "arrow_downward" : "arrow_upward"} size={14} />
        </button>

        {/* More Filters Toggle */}
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 border text-[11px] font-medium transition-colors cursor-pointer ml-auto ${
            showAdvanced || hasAdvancedFilters
              ? "bg-[#EAE7DF] border-[#9E9B90] text-[#171A18]"
              : "bg-[#F5F3EE] border-[#D8D5CD] text-[#5F6360] hover:text-[#171A18]"
          }`}
          title="Toggle advanced structured filters (User ID, API, Source)"
        >
          <Icon name="tune" size={14} />
          <span>More Filters</span>
          {hasAdvancedFilters && (
            <span className="w-1.5 h-1.5 rounded-full bg-[#1F5A43]" />
          )}
          <Icon name={showAdvanced ? "expand_less" : "expand_more"} size={14} />
        </button>
      </div>

      {/* Custom Date-Time Selection Panel (active when timePreset === 'custom') */}
      {timePreset === "custom" && (
        <div className="p-3 bg-white border border-[#D8D5CD] rounded-xl flex flex-col gap-2 shadow-2xs">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#171A18]">
              <Icon name="date_range" size={14} className="text-[#1F5A43]" />
              <span>Custom UTC Time Range</span>
            </div>
            <span className="text-[10px] text-[#9E9B90] font-mono">
              Validated ISO boundaries (start &lt; end)
            </span>
          </div>

          <form
            onSubmit={handleApplyCustomRange}
            className="flex flex-wrap items-center gap-2 text-[11px]"
          >
            <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] rounded-lg px-2.5 py-1">
              <span className="text-[#5F6360] font-medium">From:</span>
              <input
                type="datetime-local"
                value={localStart}
                onChange={(e) => {
                  setLocalStart(e.target.value);
                  setLocalError(null);
                }}
                className="bg-transparent font-mono text-[#171A18] text-[11px] border-0 outline-none p-0"
              />
            </div>

            <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] rounded-lg px-2.5 py-1">
              <span className="text-[#5F6360] font-medium">To:</span>
              <input
                type="datetime-local"
                value={localEnd}
                onChange={(e) => {
                  setLocalEnd(e.target.value);
                  setLocalError(null);
                }}
                className="bg-transparent font-mono text-[#171A18] text-[11px] border-0 outline-none p-0"
              />
            </div>

            <button
              type="submit"
              className="px-3 py-1 rounded-lg bg-[#1F5A43] hover:bg-[#184634] text-white font-medium text-[11px] transition-colors cursor-pointer shadow-2xs"
            >
              Apply Range
            </button>

            <button
              type="button"
              onClick={() => {
                onTimePresetChange("24h");
                setLocalError(null);
              }}
              className="px-2.5 py-1 rounded-lg border border-[#D8D5CD] bg-white hover:bg-[#F5F3EE] text-[#5F6360] font-medium text-[11px] transition-colors cursor-pointer"
            >
              Cancel
            </button>
          </form>

          {(localError || customRangeError) && (
            <div className="flex items-center gap-1.5 text-[11px] text-[#A34B4B] bg-[#FEECEB] px-2.5 py-1 rounded-md border border-[#FCD3CE]">
              <Icon name="error" size={13} />
              <span>{localError || customRangeError}</span>
            </div>
          )}
        </div>
      )}

      {/* Advanced Filter Row (User ID, API, Source) */}
      {showAdvanced && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 bg-white border border-[#D8D5CD] rounded-xl shadow-2xs">
          {/* User ID Filter */}
          <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1 text-[#5F6360]">
            <Icon name="person" size={14} className="text-[#9E9B90]" />
            <span className="font-medium text-[#5F6360]">User ID:</span>
            <input
              type="text"
              value={userIdFilter}
              onChange={(e) => onUserIdChange(e.target.value)}
              placeholder="e.g. usr_1042"
              className="bg-transparent text-[11px] font-mono text-[#171A18] border-0 outline-none focus:ring-0 p-0 w-24 sm:w-28 placeholder:text-[#9E9B90]"
            />
            {userIdFilter && (
              <button
                type="button"
                onClick={() => onUserIdChange("")}
                className="text-[#9E9B90] hover:text-[#171A18] ml-0.5 cursor-pointer p-0.5"
                title="Clear user ID filter"
              >
                <Icon name="close" size={11} />
              </button>
            )}
          </div>

          {/* API / Endpoint Filter */}
          <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1 text-[#5F6360]">
            <Icon name="http" size={14} className="text-[#9E9B90]" />
            <span className="font-medium text-[#5F6360]">API:</span>
            <input
              type="text"
              value={apiFilter}
              onChange={(e) => onApiChange(e.target.value)}
              placeholder="e.g. /api/v1/checkout"
              className="bg-transparent text-[11px] font-mono text-[#171A18] border-0 outline-none focus:ring-0 p-0 w-28 sm:w-36 placeholder:text-[#9E9B90]"
            />
            {apiFilter && (
              <button
                type="button"
                onClick={() => onApiChange("")}
                className="text-[#9E9B90] hover:text-[#171A18] ml-0.5 cursor-pointer p-0.5"
                title="Clear API filter"
              >
                <Icon name="close" size={11} />
              </button>
            )}
          </div>

          {/* Source Format Filter */}
          <div className="flex items-center gap-1.5 bg-[#F5F3EE] border border-[#D8D5CD] hover:border-[#1F5A43] transition-colors rounded-lg px-2.5 py-1 text-[#5F6360]">
            <Icon name="dataset" size={14} className="text-[#9E9B90]" />
            <span className="font-medium text-[#5F6360]">Source:</span>
            <select
              value={sourceFilter}
              onChange={(e) => onSourceChange(e.target.value)}
              className="bg-transparent text-[11px] font-mono font-medium text-[#171A18] border-0 outline-none focus:ring-0 p-0 cursor-pointer"
            >
              <option value="">All Formats</option>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
              <option value="syslog">Syslog</option>
              <option value="app_log">App Log</option>
            </select>
            {sourceFilter && (
              <button
                type="button"
                onClick={() => onSourceChange("")}
                className="text-[#9E9B90] hover:text-[#171A18] ml-0.5 cursor-pointer p-0.5"
                title="Clear source filter"
              >
                <Icon name="close" size={11} />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

