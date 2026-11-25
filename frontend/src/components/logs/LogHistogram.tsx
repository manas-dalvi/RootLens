"use client";

import React, { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import type { LogTimeSeriesResponse, TimeBucket } from "@/lib/api/logs";
import {
  MAX_ALLOWED_BUCKETS,
  getIntervalDurationMs,
  calculateOptimalInterval,
  selectOptimalInterval,
  formatBucketLabel,
  formatFullUtc,
  type SelectedBucketRange,
  type OptimalIntervalResult,
} from "./histogramUtils";

export {
  MAX_ALLOWED_BUCKETS,
  getIntervalDurationMs,
  calculateOptimalInterval,
  selectOptimalInterval,
  formatBucketLabel,
  formatFullUtc,
  type SelectedBucketRange,
  type OptimalIntervalResult,
};

export interface LogHistogramProps {
  timeSeries: LogTimeSeriesResponse | null;
  isLoading: boolean;
  error: string | null;
  selectedBucket: SelectedBucketRange | null;
  onSelectBucket: (bucketRange: SelectedBucketRange | null) => void;
  onRetry?: () => void;
}

export function LogHistogram({
  timeSeries,
  isLoading,
  error,
  selectedBucket,
  onSelectBucket,
  onRetry,
}: LogHistogramProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const buckets = timeSeries?.buckets || [];
  const interval = timeSeries?.interval || "30m";
  const intervalMs = getIntervalDurationMs(interval);
  const totalLogs = timeSeries?.total_logs ?? 0;

  // Find max doc count across buckets for proportional scaling
  const maxCount = Math.max(1, ...buckets.map((b) => b.doc_count));

  // Determine if a bucket is currently selected
  const isBucketSelected = (b: TimeBucket) => {
    if (!selectedBucket) return false;
    const bStart = new Date(b.timestamp).getTime();
    const targetStart = new Date(selectedBucket.start).getTime();
    return Math.abs(bStart - targetStart) < 1000;
  };

  const handleBarClick = (b: TimeBucket) => {
    if (isBucketSelected(b)) {
      // Toggle off
      onSelectBucket(null);
      return;
    }

    const startTs = new Date(b.timestamp).getTime();
    const endTs = startTs + intervalMs;
    onSelectBucket({
      start: new Date(startTs).toISOString(),
      end: new Date(endTs).toISOString(),
      count: b.doc_count,
    });
  };

  return (
    <div className="bg-white rounded-xl border border-[#D8D5CD] p-4 flex flex-col gap-3 shadow-2xs">
      {/* Top Header: Title, Aggregation Metadata, Active Selection, Reset Action */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#D8D5CD] pb-2.5">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-1.5 font-semibold text-[13px] text-[#171A18]">
            <Icon name="bar_chart" size={17} className="text-[#1F5A43]" />
            <span>Log Volume Distribution</span>
          </div>

          <div className="flex items-center gap-1 text-[11px] text-[#5F6360] font-mono">
            <span className="bg-[#F5F3EE] px-1.5 py-0.5 rounded border border-[#D8D5CD]">
              Bucket: {interval}
            </span>
            <span>·</span>
            <span>{totalLogs.toLocaleString()} total events</span>
          </div>
        </div>

        {/* Selected Bucket Indicator and Reset Button */}
        {selectedBucket ? (
          <div className="flex items-center gap-2 text-[11px] bg-[#EEF3FF] border border-[#BFDBFE] px-2.5 py-1 rounded-lg">
            <div className="flex items-center gap-1 text-[#1F5A43] font-mono font-medium">
              <span className="w-2 h-2 rounded-full bg-[#1F5A43]" />
              <span>
                Focused window: {formatFullUtc(selectedBucket.start)} →{" "}
                {formatFullUtc(selectedBucket.end)}
              </span>
              <span className="text-[#5F6360]">({selectedBucket.count} events)</span>
            </div>
            <button
              type="button"
              onClick={() => onSelectBucket(null)}
              className="text-[#1F5A43] hover:text-[#184634] font-semibold underline transition-colors cursor-pointer ml-1"
            >
              Reset to range
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-[11px] text-[#9E9B90]">
            <span className="hidden md:inline">Click any column to filter logs to that time window</span>
          </div>
        )}
      </div>

      {/* Main Chart Body */}
      {isLoading ? (
        <div className="h-28 flex items-center justify-center gap-2 text-[#5F6360] text-[12px]">
          <Icon name="sync" size={16} className="animate-spin text-[#1F5A43]" />
          <span>Calculating OpenSearch time-series histogram...</span>
        </div>
      ) : error ? (
        <div className="h-28 flex items-center justify-between p-3 bg-[#FEECEB] border border-[#FCD3CE] rounded-lg text-[#A34B4B] text-[12px]">
          <div className="flex items-center gap-2">
            <Icon name="warning" size={18} />
            <span>Failed to load log distribution: {error}</span>
          </div>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="px-2.5 py-1 bg-white border border-[#FCD3CE] text-[11px] font-medium rounded hover:bg-[#FEECEB] cursor-pointer"
            >
              Retry
            </button>
          )}
        </div>
      ) : buckets.length === 0 ? (
        <div className="h-28 flex flex-col items-center justify-center text-center gap-1 text-[#5F6360] text-[12px]">
          <p className="font-medium text-[#171A18]">No log events in this interval</p>
          <p className="text-[11px] text-[#9E9B90]">
            Adjust the time range or search query to see volume distribution.
          </p>
        </div>
      ) : (
        <div className="relative flex flex-col gap-1 w-full select-none">
          {/* Bars Container */}
          <div className="h-24 flex items-end gap-1 w-full pt-2">
            {buckets.map((b, idx) => {
              const heightPct = Math.max(6, Math.round((b.doc_count / maxCount) * 100));
              const isSelected = isBucketSelected(b);
              const isHovered = hoveredIndex === idx;

              // Severity counts for breakdown
              const sevs = b.severities || {};
              const errCount = (sevs.ERROR || 0) + (sevs.FATAL || 0) + (sevs.CRITICAL || 0);
              const warnCount = (sevs.WARN || 0) + (sevs.WARNING || 0);
              const total = b.doc_count;
              const hasErrors = errCount > 0;
              const hasWarnings = warnCount > 0;

              let barBg = "bg-[#D8D5CD] hover:bg-[#1F5A43]";
              if (hasErrors) {
                barBg = "bg-[#E63316] hover:bg-[#C92911]";
              } else if (hasWarnings) {
                barBg = "bg-[#B77900] hover:bg-[#9B6600]";
              } else if (total > 0) {
                barBg = "bg-[#1F5A43] hover:bg-[#184634]";
              }

              return (
                <div
                  key={b.timestamp}
                  onClick={() => handleBarClick(b)}
                  onMouseEnter={() => setHoveredIndex(idx)}
                  onMouseLeave={() => setHoveredIndex(null)}
                  className={`flex-1 relative flex items-end justify-center rounded-t-sm transition-all cursor-pointer ${
                    isSelected
                      ? "ring-2 ring-[#1F5A43] ring-offset-1 ring-offset-white opacity-100 z-10"
                      : isHovered
                      ? "opacity-95"
                      : "opacity-85"
                  }`}
                  style={{ height: `${heightPct}%`, minWidth: "3px" }}
                  title={`${formatFullUtc(b.timestamp)}: ${b.doc_count} logs`}
                >
                  <div
                    className={`w-full h-full rounded-t-sm transition-colors ${
                      isSelected ? "bg-[#1F5A43]" : barBg
                    }`}
                  />

                  {/* Tooltip on Hover */}
                  {isHovered && (
                    <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 bg-[#171A18] text-white text-[11px] px-2.5 py-1.5 rounded-lg shadow-lg z-30 whitespace-nowrap pointer-events-none flex flex-col gap-0.5">
                      <div className="font-mono font-semibold text-[#F5F3EE]">
                        {formatFullUtc(b.timestamp)}
                      </div>
                      <div className="flex items-center gap-2 text-[10px]">
                        <span>{b.doc_count.toLocaleString()} logs</span>
                        {hasErrors && (
                          <span className="text-[#FF8A80]">({errCount} errors)</span>
                        )}
                        {hasWarnings && (
                          <span className="text-[#FFE082]">({warnCount} warnings)</span>
                        )}
                      </div>
                      <div className="text-[9px] text-[#9E9B90] italic pt-0.5">
                        {isSelected ? "Click to deselect" : "Click to narrow search"}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Time Axis Labels (Show first, middle, and last) */}
          <div className="flex items-center justify-between text-[10px] text-[#9E9B90] font-mono px-0.5 pt-1 border-t border-[#EDEFF3]">
            {buckets.length > 0 && (
              <>
                <span>{formatBucketLabel(buckets[0].timestamp, interval)}</span>
                {buckets.length > 2 && (
                  <span>
                    {formatBucketLabel(
                      buckets[Math.floor(buckets.length / 2)].timestamp,
                      interval
                    )}
                  </span>
                )}
                <span>
                  {formatBucketLabel(
                    buckets[buckets.length - 1].timestamp,
                    interval
                  )}
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

