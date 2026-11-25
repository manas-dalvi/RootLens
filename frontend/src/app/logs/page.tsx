"use client";

import React, { useState, useEffect, useCallback, Suspense, useRef } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { LogDetailDrawer } from "@/components/logs/LogDetailDrawer";
import {
  fetchLogs,
  fetchLogById,
  fetchLogAggregations,
  ApiError,
  type LogDocument,
  type LogSearchResponse,
  type LogSearchParams,
  type LogTimeSeriesResponse,
} from "@/lib/api/logs";
import {
  LogFilterBar,
  type TimePreset,
  TIME_PRESET_LABELS,
} from "@/components/logs/LogFilterBar";
import { ActiveFilterChips } from "@/components/logs/ActiveFilterChips";
import {
  LogHistogram,
  calculateOptimalInterval,
  type SelectedBucketRange,
} from "@/components/logs/LogHistogram";
import { LogTable } from "@/components/logs/LogTable";
import { ExportModal } from "@/components/logs/ExportModal";
import {
  UserColumnPreferences,
  DEFAULT_COLUMN_PREFERENCES,
  loadStoredPreferences,
  saveStoredPreferences,
} from "@/components/logs/columnConfig";

function getStartTimeFromPreset(preset: TimePreset): string | undefined {
  const now = Date.now();
  switch (preset) {
    case "15m":
      return new Date(now - 15 * 60 * 1000).toISOString();
    case "1h":
      return new Date(now - 60 * 60 * 1000).toISOString();
    case "24h":
      return new Date(now - 24 * 60 * 60 * 1000).toISOString();
    case "7d":
      return new Date(now - 7 * 24 * 60 * 60 * 1000).toISOString();
    case "all":
    case "custom":
    default:
      return undefined;
  }
}

function LogExplorerContent() {
  const searchParams = useSearchParams();
  const urlQ = searchParams?.get("q") || "";
  const urlService = searchParams?.get("service") || "";
  const urlErrorsOnly = searchParams?.get("errors_only") === "true";
  const urlLevel = searchParams?.get("level") || "";
  const lastUrlQRef = useRef<string>(urlQ);

  // Query & Filter States
  const [searchInput, setSearchInput] = useState<string>(urlQ);
  const [activeQuery, setActiveQuery] = useState<string>(urlQ);
  const [timePreset, setTimePreset] = useState<TimePreset>("all");
  const [customStartTime, setCustomStartTime] = useState<string>("");
  const [customEndTime, setCustomEndTime] = useState<string>("");
  const [customRangeError, setCustomRangeError] = useState<string | null>(null);
  const [serviceFilter, setServiceFilter] = useState<string>(urlService);
  const [levelFilter, setLevelFilter] = useState<string>(urlLevel);
  const [errorsOnly, setErrorsOnly] = useState<boolean>(urlErrorsOnly);
  const [userIdFilter, setUserIdFilter] = useState<string>("");
  const [apiFilter, setApiFilter] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(50);
  const [sortOrder, setSortOrder] = useState<"desc" | "asc">("desc");

  // Step 2C: Configurable Column Preferences & Persistence
  const [columnPreferences, setColumnPreferences] = useState<UserColumnPreferences>(() => {
    const stored = loadStoredPreferences();
    if (stored) {
      return stored;
    }
    return {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: DEFAULT_COLUMN_PREFERENCES.messageMode,
    };
  });

  const handlePreferencesChange = useCallback((newPrefs: UserColumnPreferences) => {
    setColumnPreferences(newPrefs);
    saveStoredPreferences(newPrefs);
  }, []);

  const handleSortChange = useCallback((newOrder: "desc" | "asc") => {
    setSortOrder(newOrder);
    setCurrentPage(1);
  }, []);

  const handlePageSizeChange = useCallback((newSize: number) => {
    setPageSize(newSize);
    setCurrentPage(1);
  }, []);

  // Keep search input and query synchronized when URL search param changes via client navigation
  useEffect(() => {
    if (urlQ !== lastUrlQRef.current) {
      lastUrlQRef.current = urlQ;
      setSearchInput(urlQ);
      setActiveQuery(urlQ);
      setCurrentPage(1);
    }
  }, [urlQ]);

  const [prevUrlService, setPrevUrlService] = useState<string>(urlService);
  if (urlService !== prevUrlService) {
    setPrevUrlService(urlService);
    setServiceFilter(urlService);
  }

  const [prevUrlErrorsOnly, setPrevUrlErrorsOnly] = useState<boolean>(urlErrorsOnly);
  if (urlErrorsOnly !== prevUrlErrorsOnly) {
    setPrevUrlErrorsOnly(urlErrorsOnly);
    setErrorsOnly(urlErrorsOnly);
  }

  // Data & Request States
  const [searchResponse, setSearchResponse] = useState<LogSearchResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  // Selected Log & Drawer States
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<LogDocument | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState<boolean>(false);

  // Export State
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);

  // Re-fetch trigger counter to force manual refresh
  const [refreshIndex, setRefreshIndex] = useState<number>(0);

  // Histogram & Bucket Selection State (Step 2B)
  const [selectedBucket, setSelectedBucket] = useState<SelectedBucketRange | null>(null);
  const [timeSeries, setTimeSeries] = useState<LogTimeSeriesResponse | null>(null);
  const [isHistogramLoading, setIsHistogramLoading] = useState<boolean>(false);
  const [histogramError, setHistogramError] = useState<string | null>(null);

  // Request race-condition protection & input ref (Step 2B)
  const searchRequestIdRef = useRef<number>(0);
  const histogramRequestIdRef = useRef<number>(0);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Global keyboard shortcut: '/' to focus search input, Esc to blur (Step 2B)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeElement = document.activeElement;
      const isInput =
        activeElement instanceof HTMLInputElement ||
        activeElement instanceof HTMLTextAreaElement ||
        activeElement instanceof HTMLSelectElement;

      if (e.key === "/" && !isInput) {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === "Escape" && isInput) {
        (activeElement as HTMLElement).blur();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Aggregation / Histogram fetch effect (Step 2B)
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const currentReqId = ++histogramRequestIdRef.current;

    async function loadAggregations() {
      await Promise.resolve();
      if (cancelled) return;

      let startTime: string | undefined = undefined;
      let endTime: string | undefined = undefined;

      if (timePreset === "custom") {
        if (!customStartTime || !customEndTime) {
          setTimeSeries(null);
          return;
        }
        try {
          startTime = new Date(customStartTime).toISOString();
          endTime = new Date(customEndTime).toISOString();
        } catch {
          setTimeSeries(null);
          return;
        }
      } else {
        startTime = getStartTimeFromPreset(timePreset);
        endTime = undefined;
      }

      const intervalResult = calculateOptimalInterval(startTime, endTime, timePreset);
      if (intervalResult.error || !intervalResult.interval) {
        setHistogramError(
          intervalResult.error || "Selected date range exceeds maximum allowed limit."
        );
        setIsHistogramLoading(false);
        setTimeSeries(null);
        return;
      }

      const interval = intervalResult.interval;

      setIsHistogramLoading(true);
      setHistogramError(null);

      try {
        const data = await fetchLogAggregations(
          {
            interval,
            start_time: startTime,
            end_time: endTime,
            service: serviceFilter || undefined,
          },
          controller.signal
        );

        if (!cancelled && histogramRequestIdRef.current === currentReqId) {
          setTimeSeries(data);
        }
      } catch (err: unknown) {
        if (cancelled || histogramRequestIdRef.current !== currentReqId) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setHistogramError(
          err instanceof Error ? err.message : "Failed to load log volume histogram"
        );
      } finally {
        if (!cancelled && histogramRequestIdRef.current === currentReqId) {
          setIsHistogramLoading(false);
        }
      }
    }

    void loadAggregations();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [timePreset, customStartTime, customEndTime, serviceFilter, refreshIndex]);

  // Data loading effect synchronized with search filters & pagination
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const currentReqId = ++searchRequestIdRef.current;

    async function execute() {
      // Defer to microtask boundary so state updates occur asynchronously
      await Promise.resolve();
      if (cancelled) return;

      setIsLoading(true);
      setError(null);

      let startTime: string | undefined = undefined;
      let endTime: string | undefined = undefined;

      if (selectedBucket) {
        // If a specific histogram bucket is selected, bound search to that bucket window
        startTime = selectedBucket.start;
        endTime = selectedBucket.end;
      } else if (timePreset === "custom") {
        if (!customStartTime || !customEndTime) {
          // Incomplete custom range: do not trigger an unconstrained search
          setIsLoading(false);
          return;
        }
        try {
          startTime = new Date(customStartTime).toISOString();
          endTime = new Date(customEndTime).toISOString();
        } catch {
          setIsLoading(false);
          return;
        }
      } else {
        startTime = getStartTimeFromPreset(timePreset);
        endTime = undefined;
      }

      const params: LogSearchParams = {
        q: activeQuery || undefined,
        start_time: startTime,
        end_time: endTime,
        service: serviceFilter || undefined,
        severity: levelFilter || undefined,
        errors_only: errorsOnly || undefined,
        user_id: userIdFilter || undefined,
        api: apiFilter || undefined,
        source: sourceFilter || undefined,
        page: currentPage,
        page_size: pageSize,
        sort_order: sortOrder,
      };

      const t0 = performance.now();
      try {
        let response = await fetchLogs(params, controller.signal);
        if (cancelled || searchRequestIdRef.current !== currentReqId) return;

        // If search returned 0 items and an activeQuery is present,
        // check if activeQuery corresponds to a specific log document ID.
        // Citation deep-links use exact OpenSearch document IDs (_id), which
        // are retrieved via GET /api/v1/logs/{id} (ids query), whereas the search
        // endpoint queries message, service, host, and trace/span IDs via multi_match.
        if (response.total === 0 && activeQuery && !cancelled) {
          try {
            const singleDoc = await fetchLogById(activeQuery, controller.signal);
            if (!cancelled && singleDoc && searchRequestIdRef.current === currentReqId) {
              response = {
                items: [singleDoc],
                page: 1,
                page_size: pageSize,
                total: 1,
                total_pages: 1,
              };
              setSelectedLogId(singleDoc.id);
              setSelectedLog(singleDoc);
            }
          } catch {
            // Not an exact document ID or document not found; keep standard empty response
          }
        }

        if (cancelled || searchRequestIdRef.current !== currentReqId) return;

        const t1 = performance.now();
        setLatencyMs(Math.round(t1 - t0));
        setSearchResponse(response);

        // Update selected log if it exists in current result items
        if (selectedLogId) {
          const match = response.items.find((item) => item.id === selectedLogId);
          if (match) {
            setSelectedLog(match);
          }
        }
      } catch (err: unknown) {
        if (cancelled || searchRequestIdRef.current !== currentReqId) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError) {
          setError(err);
        } else {
          setError(
            new ApiError(0, err instanceof Error ? err.message : "Failed to execute log query")
          );
        }
      } finally {
        if (!cancelled && searchRequestIdRef.current === currentReqId) {
          setIsLoading(false);
        }
      }
    }

    void execute();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    activeQuery,
    selectedBucket,
    timePreset,
    customStartTime,
    customEndTime,
    serviceFilter,
    levelFilter,
    errorsOnly,
    userIdFilter,
    apiFilter,
    sourceFilter,
    currentPage,
    pageSize,
    sortOrder,
    refreshIndex,
    selectedLogId,
  ]);

  // Handle Search Input Submission
  const handleRunQuery = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setCurrentPage(1);
    if (activeQuery === searchInput.trim()) {
      setRefreshIndex((prev) => prev + 1);
    } else {
      setActiveQuery(searchInput.trim());
    }
  };

  // Clear all filters back to defaults
  const handleClearFilters = () => {
    setSearchInput("");
    setActiveQuery("");
    setTimePreset("24h");
    setCustomStartTime("");
    setCustomEndTime("");
    setCustomRangeError(null);
    setSelectedBucket(null);
    setServiceFilter("");
    setLevelFilter("");
    setErrorsOnly(false);
    setUserIdFilter("");
    setApiFilter("");
    setSourceFilter("");
    setCurrentPage(1);
    setSelectedLogId(null);
    setSelectedLog(null);
  };

  // Handle Log Selection
  const handleSelectLog = useCallback(async (logItem: LogDocument) => {
    setSelectedLogId(logItem.id);
    setSelectedLog(logItem);

    setIsLoadingDetail(true);
    try {
      const detailed = await fetchLogById(logItem.id);
      setSelectedLog(detailed);
    } catch {
      // Retain existing log item if detailed fetch fails
    } finally {
      setIsLoadingDetail(false);
    }
  }, []);

  // Filter by Trace action
  const handleFilterByTrace = useCallback((traceId: string) => {
    setSearchInput(traceId);
    setActiveQuery(traceId);
    setCurrentPage(1);
  }, []);

  // Switch to All Time handler (Step 2C Phase 5)
  const handleSwitchToAllTime = useCallback(() => {
    setTimePreset("all");
    setCustomStartTime("");
    setCustomEndTime("");
    setCustomRangeError(null);
    setSelectedBucket(null);
    setCurrentPage(1);
  }, []);

  const totalEvents = searchResponse?.total ?? 0;
  const totalPages = searchResponse?.total_pages ?? 0;
  const items = searchResponse?.items ?? [];

  return (
    <div className="flex flex-col w-full min-h-screen">
      {/* Header Bar */}
      <div className="px-6 py-4 flex flex-col md:flex-row md:items-center md:justify-between gap-3 bg-white border-b border-[#EDEFF3]">
        <div className="flex flex-col">
          <div className="flex items-center gap-2.5">
            <h1 className="text-[20px] font-semibold text-[#1B1A1A] tracking-tight">
              Log Explorer
            </h1>
            <StatusBadge label="REST SEARCH" variant="info" dot />
            <span className="text-[11px] text-[#9B9FA3] font-mono hidden sm:inline">
              OpenSearch 2.x
            </span>
          </div>
          <p className="text-[13px] text-[#606366] mt-0.5">
            Search, filter, and inspect enterprise log events and correlated traces.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          {exportNotice && (
            <span className="text-[12px] font-medium text-[#2E7D32] bg-[#EDF9ED] px-2.5 py-1 rounded-lg border border-[#C5ECC4]">
              {exportNotice}
            </span>
          )}
          <Link
            href="/logs/upload"
            className="px-3.5 py-2 rounded-lg text-[13px] font-medium text-[#4D80FC] bg-[#EEF3FF] border border-[#BFDBFE] hover:bg-[#E2EDFF] transition-colors flex items-center gap-1.5"
          >
            <Icon name="upload_file" size={16} />
            <span>Upload Logs</span>
          </Link>
          <button
            type="button"
            onClick={() => setIsExportModalOpen(true)}
            disabled={totalEvents === 0}
            title={totalEvents === 0 ? "No records to export" : "Export logs to JSON or CSV"}
            className={`px-3.5 py-2 rounded-lg text-[13px] font-medium border flex items-center gap-1.5 transition-colors ${
              totalEvents > 0
                ? "text-[#1F5A43] bg-[#EBF2EE] border-[#1F5A43]/30 hover:bg-[#DDEAE3] cursor-pointer"
                : "text-[#9B9FA3] bg-white border-[#EDEFF3] cursor-not-allowed"
            }`}
          >
            <Icon name="file_download" size={16} />
            <span>Export</span>
          </button>
        </div>
      </div>

      {/* Query Bar & Filters Section */}
      <div className="p-5 flex flex-col gap-3 bg-white border-b border-[#EDEFF3]">
        <form onSubmit={handleRunQuery} className="flex flex-col lg:flex-row items-stretch gap-2.5">
          <div className="flex-1 flex items-center bg-[#F5F6F9] border border-[#EDEFF3] focus-within:border-[#4D80FC] rounded-lg px-3.5 py-1.5 gap-2.5 transition-colors">
            <Icon name="terminal" size={18} className="text-[#9B9FA3]" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  (e.target as HTMLInputElement).blur();
                }
              }}
              placeholder="Search logs by keyword, message, host, service, or trace ID... (Press '/' to focus)"
              className="w-full bg-transparent text-[#1B1A1A] font-mono text-[12px] outline-none border-0 focus:ring-0 p-0 placeholder:text-[#9B9FA3]"
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => {
                  setSearchInput("");
                  setActiveQuery("");
                  setCurrentPage(1);
                }}
                className="text-[#9B9FA3] hover:text-[#1B1A1A] transition-colors p-1 cursor-pointer"
                title="Clear search input (Esc to unfocus)"
              >
                <Icon name="close" size={14} />
              </button>
            )}
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="px-5 py-2 rounded-lg text-[13px] font-medium text-white bg-[#4D80FC] hover:bg-[#396fe4] transition-colors flex items-center justify-center gap-1.5 shadow-xs disabled:opacity-60 cursor-pointer"
          >
            {isLoading ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <Icon name="play_arrow" size={16} />
            )}
            <span>Run Query</span>
          </button>
        </form>

        {/* Log Filter Bar Component (Step 2A) */}
        <LogFilterBar
          timePreset={timePreset}
          onTimePresetChange={(preset) => {
            setSelectedBucket(null);
            setTimePreset(preset);
            if (preset !== "custom") {
              setCustomStartTime("");
              setCustomEndTime("");
              setCustomRangeError(null);
            }
            setCurrentPage(1);
          }}
          customStartTime={customStartTime}
          customEndTime={customEndTime}
          onCustomRangeApply={(start, end) => {
            setSelectedBucket(null);
            setCustomStartTime(start);
            setCustomEndTime(end);
            setCustomRangeError(null);
            setCurrentPage(1);
          }}
          customRangeError={customRangeError}
          serviceFilter={serviceFilter}
          onServiceChange={(service) => {
            setSelectedBucket(null);
            setServiceFilter(service);
            setCurrentPage(1);
          }}
          severityFilter={levelFilter}
          onSeverityChange={(level) => {
            setLevelFilter(level);
            setCurrentPage(1);
          }}
          errorsOnly={errorsOnly}
          onErrorsOnlyChange={(errOnly) => {
            setErrorsOnly(errOnly);
            setCurrentPage(1);
          }}
          userIdFilter={userIdFilter}
          onUserIdChange={(uid) => {
            setUserIdFilter(uid);
            setCurrentPage(1);
          }}
          apiFilter={apiFilter}
          onApiChange={(api) => {
            setApiFilter(api);
            setCurrentPage(1);
          }}
          sourceFilter={sourceFilter}
          onSourceChange={(src) => {
            setSourceFilter(src);
            setCurrentPage(1);
          }}
          sortOrder={sortOrder}
          onSortOrderChange={handleSortChange}
        />

        {/* Active Filter Chips Component (Step 2A & 2B) */}
        <ActiveFilterChips
          query={activeQuery}
          timePreset={timePreset}
          timePresetLabel={TIME_PRESET_LABELS[timePreset]}
          customStartTime={customStartTime}
          customEndTime={customEndTime}
          selectedBucket={selectedBucket}
          service={serviceFilter}
          severity={levelFilter}
          errorsOnly={errorsOnly}
          userId={userIdFilter}
          api={apiFilter}
          source={sourceFilter}
          onRemoveQuery={() => {
            setSearchInput("");
            setActiveQuery("");
            setCurrentPage(1);
          }}
          onResetTimeRange={() => {
            setSelectedBucket(null);
            setTimePreset("24h");
            setCustomStartTime("");
            setCustomEndTime("");
            setCustomRangeError(null);
            setCurrentPage(1);
          }}
          onClearBucket={() => {
            setSelectedBucket(null);
            setCurrentPage(1);
          }}
          onRemoveService={() => {
            setSelectedBucket(null);
            setServiceFilter("");
            setCurrentPage(1);
          }}
          onRemoveSeverity={() => {
            setLevelFilter("");
            setCurrentPage(1);
          }}
          onRemoveErrorsOnly={() => {
            setErrorsOnly(false);
            setCurrentPage(1);
          }}
          onRemoveUserId={() => {
            setUserIdFilter("");
            setCurrentPage(1);
          }}
          onRemoveApi={() => {
            setApiFilter("");
            setCurrentPage(1);
          }}
          onRemoveSource={() => {
            setSourceFilter("");
            setCurrentPage(1);
          }}
          onResetAll={handleClearFilters}
        />

        {/* Status / Query Metadata Bar */}
        <div className="flex items-center justify-between px-3.5 py-2 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-[12px] flex-wrap gap-2">
          <div className="flex items-center gap-2 text-[#606366]">
            <span className="font-mono text-[12px] text-[#1B1A1A] font-semibold">
              {totalEvents.toLocaleString()}
            </span>
            <span>events matching criteria</span>
            <span className="text-[#9B9FA3]">·</span>
            <div className="flex items-center gap-1.5 text-[11px]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#4D80FC]"></span>
              <span>REST Search Active</span>
            </div>
          </div>
          <div className="flex items-center gap-3 font-mono text-[11px] text-[#9B9FA3]">
            {latencyMs !== null && (
              <div className="flex items-center gap-1">
                <Icon name="speed" size={14} />
                <span>Latency: {latencyMs}ms</span>
              </div>
            )}
            {totalPages > 0 && (
              <span>
                Page {currentPage} of {totalPages}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* OpenSearch 503 Service Unavailable Alert Banner */}
      {error && error.isServiceUnavailable && (
        <div className="m-6 p-4 rounded-xl bg-[#FEF7E6] border border-[#FDE8B3] flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[#FDE8B3] text-[#B77900] flex items-center justify-center shrink-0">
              <Icon name="warning" size={20} />
            </div>
            <div className="flex flex-col">
              <span className="text-[14px] font-semibold text-[#1B1A1A]">
                OpenSearch Cluster Unavailable (HTTP 503)
              </span>
              <span className="text-[12px] text-[#606366]">
                {error.detail ||
                  "The OpenSearch cluster could not be reached on localhost:9200. Ensure the OpenSearch daemon is running."}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setRefreshIndex((prev) => prev + 1)}
            className="px-4 py-2 rounded-lg text-[13px] font-medium text-[#B77900] bg-white border border-[#FDE8B3] hover:bg-[#FEF7E6] transition-colors shrink-0 flex items-center justify-center gap-1.5 cursor-pointer"
          >
            <Icon name="refresh" size={15} />
            <span>Retry Search</span>
          </button>
        </div>
      )}

      {/* General Non-503 Error Alert */}
      {error && !error.isServiceUnavailable && (
        <div className="m-6 p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-between gap-3 shadow-xs">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-[#FCD3CE] text-[#E63316] flex items-center justify-center shrink-0">
              <Icon name="error" size={18} />
            </div>
            <div className="flex flex-col">
              <span className="text-[13px] font-semibold text-[#E63316]">
                Query Execution Error ({error.status || "Network"})
              </span>
              <span className="text-[12px] text-[#606366]">
                {error.detail || error.message}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setRefreshIndex((prev) => prev + 1)}
            className="px-3.5 py-1.5 rounded-lg text-[12px] font-medium text-[#E63316] bg-white border border-[#FCD3CE] hover:bg-[#FEECEB] transition-colors shrink-0 cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}

      {/* Main Container: Log Table on Left, Detail Drawer on Right */}
      <div className="logs-layout-grid p-4 sm:p-6">
        <div className="flex-1 min-w-0 w-full flex flex-col gap-4">
          {/* Interactive Log Volume Histogram (Step 2B) */}
          <LogHistogram
            timeSeries={timeSeries}
            isLoading={isHistogramLoading}
            error={histogramError}
            selectedBucket={selectedBucket}
            onSelectBucket={(bucket) => {
              setSelectedBucket(bucket);
              setCurrentPage(1);
            }}
            onRetry={() => setRefreshIndex((prev) => prev + 1)}
          />

          {/* Enterprise Log Table with dynamic columns & pagination (Step 2C) */}
          <LogTable
            logs={items}
            isLoading={isLoading}
            selectedLogId={selectedLogId}
            onSelectLog={handleSelectLog}
            preferences={columnPreferences}
            onPreferencesChange={handlePreferencesChange}
            sortOrder={sortOrder}
            onSortChange={handleSortChange}
            currentPage={currentPage}
            totalPages={totalPages}
            totalEvents={totalEvents}
            pageSize={pageSize}
            onPageChange={setCurrentPage}
            onPageSizeChange={handlePageSizeChange}
            onClearFilters={handleClearFilters}
            hasActiveFilters={Boolean(
              activeQuery ||
                timePreset !== "24h" ||
                serviceFilter ||
                levelFilter ||
                errorsOnly ||
                userIdFilter ||
                apiFilter ||
                sourceFilter ||
                selectedBucket
            )}
            latencyMs={latencyMs}
            timePreset={timePreset}
            onSwitchToAllTime={handleSwitchToAllTime}
          />
        </div>

        {/* Right Column: Log Detail Inspection Drawer */}
        <div className="w-full min-w-0">
          <LogDetailDrawer
            log={selectedLog}
            isLoading={isLoadingDetail}
            onClose={() => {
              setSelectedLogId(null);
              setSelectedLog(null);
            }}
            onFilterByTrace={handleFilterByTrace}
          />
        </div>
      </div>

      {/* Export Modal (Milestone 02 - Step 2C - Phase 8) */}
      <ExportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        currentPageItems={items}
        currentPage={currentPage}
        totalEvents={totalEvents}
        searchParams={{
          q: activeQuery || undefined,
          start_time: selectedBucket
            ? selectedBucket.start
            : timePreset === "custom" && customStartTime
            ? new Date(customStartTime).toISOString()
            : getStartTimeFromPreset(timePreset),
          end_time: selectedBucket
            ? selectedBucket.end
            : timePreset === "custom" && customEndTime
            ? new Date(customEndTime).toISOString()
            : undefined,
          service: serviceFilter || undefined,
          severity: levelFilter || undefined,
          errors_only: errorsOnly || undefined,
          user_id: userIdFilter || undefined,
          api: apiFilter || undefined,
          source: sourceFilter || undefined,
          sort_order: sortOrder,
        }}
        columnPreferences={columnPreferences}
        onExportSuccess={(count, fmt) => {
          setExportNotice(`Exported ${count.toLocaleString()} records as ${fmt.toUpperCase()}`);
          setTimeout(() => setExportNotice(null), 4000);
        }}
      />
    </div>
  );
}

export default function LogExplorerPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col w-full min-h-screen items-center justify-center p-8 bg-[#FAFBFD]">
          <div className="w-8 h-8 border-2 border-[#4D80FC] border-t-transparent rounded-full animate-spin mb-3" />
          <span className="text-[13px] text-[#606366] font-medium">Loading Log Explorer...</span>
        </div>
      }
    >
      <LogExplorerContent />
    </Suspense>
  );
}
