"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import {
  fetchDashboardMetrics,
  fetchLogTimeSeries,
  fetchSignificantEvents,
  DashboardMetrics,
  LogTimeSeriesResponse,
  SignificantEvent,
} from "@/lib/api/dashboard";
import { fetchIncidents, IncidentSummaryItem } from "@/lib/api/incidents";
import { ApiError } from "@/lib/api/logs";

const ACTIVE_STATUS_QUERIES = ["DETECTED", "INVESTIGATING", "MITIGATED"] as const;

interface ActiveIncidentsData {
  count: number;
  items: IncidentSummaryItem[];
}

async function fetchActiveIncidents(signal?: AbortSignal): Promise<ActiveIncidentsData> {
  const responses = await Promise.all(
    ACTIVE_STATUS_QUERIES.map((status) =>
      fetchIncidents({ status, page_size: 5 }, signal)
    )
  );

  const totalCount = responses.reduce((acc, res) => acc + (res.total || 0), 0);
  const items = responses.flatMap((res) => res.items || []);
  return { count: totalCount, items };
}

type GranularityPreset = "1H" | "6H" | "24H" | "7D" | "ALL";

interface GranularityConfig {
  interval: string;
  durationMs: number | null;
  label: string;
}

const GRANULARITY_CONFIGS: Record<GranularityPreset, GranularityConfig> = {
  "1H": { interval: "2m", durationMs: 1 * 60 * 60 * 1000, label: "Last 1 Hour" },
  "6H": { interval: "15m", durationMs: 6 * 60 * 60 * 1000, label: "Last 6 Hours" },
  "24H": { interval: "1h", durationMs: 24 * 60 * 60 * 1000, label: "Last 24 Hours" },
  "7D": { interval: "6h", durationMs: 7 * 24 * 60 * 60 * 1000, label: "Last 7 Days" },
  "ALL": { interval: "1h", durationMs: null, label: "All Indexed Telemetry" },
};

function buildSvgPath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const cp1x = prev.x + (curr.x - prev.x) / 2;
    const cp2x = cp1x;
    d += ` C ${cp1x} ${prev.y}, ${cp2x} ${curr.y}, ${curr.x} ${curr.y}`;
  }
  return d;
}

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [timeSeries, setTimeSeries] = useState<LogTimeSeriesResponse | null>(null);
  const [significantEvents, setSignificantEvents] = useState<SignificantEvent[]>([]);
  // Default to ALL so indexed demonstration and benchmark datasets render genuine aggregation curves on load
  const [selectedGranularity, setSelectedGranularity] = useState<GranularityPreset>("ALL");

  // Incident KPI state synchronized between top card and active incidents panel
  const [activeIncidentsCount, setActiveIncidentsCount] = useState<number | null>(null);
  const [activeIncidents, setActiveIncidents] = useState<IncidentSummaryItem[]>([]);
  const [isLoadingIncidents, setIsLoadingIncidents] = useState<boolean>(true);
  const [incidentError, setIncidentError] = useState<string | null>(null);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isLoadingChart, setIsLoadingChart] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true);
    setRefreshTrigger((prev) => prev + 1);
  }, []);

  // Synchronize with global Header refresh events
  useEffect(() => {
    const handleGlobalRefresh = () => {
      handleRefresh();
    };
    window.addEventListener("rootlens:refresh", handleGlobalRefresh);
    return () => {
      window.removeEventListener("rootlens:refresh", handleGlobalRefresh);
    };
  }, [handleRefresh]);

  // Broadcast refresh status to Header
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("rootlens:refresh-status", {
        detail: { isRefreshing },
      })
    );
  }, [isRefreshing]);

  // Main data loading effect
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function execute() {
      await Promise.resolve();
      if (cancelled) return;

      if (refreshTrigger === 0) {
        setIsLoading(true);
        setIsLoadingIncidents(true);
      }
      setError(null);
      setIncidentError(null);

      try {
        const config = GRANULARITY_CONFIGS[selectedGranularity];
        let startTime: string | undefined;
        let endTime: string | undefined;

        if (config.durationMs !== null) {
          const now = new Date();
          startTime = new Date(now.getTime() - config.durationMs).toISOString();
          endTime = now.toISOString();
        }

        const [dashboardData, timeSeriesData, eventsData, incidentResult] = await Promise.all([
          fetchDashboardMetrics(controller.signal),
          fetchLogTimeSeries(
            {
              interval: config.interval,
              start_time: startTime,
              end_time: endTime,
            },
            controller.signal
          ),
          fetchSignificantEvents(
            {
              start_time: startTime,
              end_time: endTime,
              error_threshold: 1,
            },
            controller.signal
          ),
          fetchActiveIncidents(controller.signal)
            .then((res) => ({ data: res }))
            .catch((err: unknown) => {
              return {
                error:
                  err instanceof Error ? err.message : "Failed to load incidents from catalogue",
              };
            }),
        ]);

        if (cancelled) return;
        setMetrics(dashboardData);
        setTimeSeries(timeSeriesData);
        setSignificantEvents(eventsData.events);

        if ("error" in incidentResult) {
          setActiveIncidentsCount(null);
          setActiveIncidents([]);
          setIncidentError(incidentResult.error);
        } else {
          setActiveIncidentsCount(incidentResult.data.count);
          setActiveIncidents(incidentResult.data.items);
          setIncidentError(null);
        }
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError) {
          setError(err.message || "Failed to connect to backend telemetry service.");
        } else {
          setError("An unexpected error occurred while fetching dashboard metrics.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
          setIsLoadingIncidents(false);
          setIsRefreshing(false);
          setIsLoadingChart(false);
        }
      }
    }

    execute();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [refreshTrigger, selectedGranularity]);

  // Tab change handler for chart granularity
  const handleGranularityChange = useCallback((preset: GranularityPreset) => {
    setSelectedGranularity(preset);
    setIsLoadingChart(true);
  }, []);

  const formatShortTime = (ts: string | null | undefined) => {
    if (!ts) return "—";
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return (
        d.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: "UTC",
        }) + " UTC"
      );
    } catch {
      return ts;
    }
  };

  const readinessStatus = metrics?.readiness?.status || "offline";
  const clusterName =
    metrics?.readiness?.dependencies?.opensearch?.cluster_name ||
    metrics?.readiness?.dependencies?.opensearch?.status ||
    "local";

  // Check if telemetry data represents an indexed historical/benchmark dataset
  const latestLogTimestamp = useMemo(() => {
    if (metrics?.recentLogs && metrics.recentLogs.length > 0) {
      return metrics.recentLogs[0].timestamp;
    }
    if (timeSeries?.buckets && timeSeries.buckets.length > 0) {
      const active = timeSeries.buckets.filter((b) => b.doc_count > 0);
      if (active.length > 0) {
        return active[active.length - 1].timestamp;
      }
    }
    return null;
  }, [metrics, timeSeries]);

  const isHistoricalData = useMemo(() => {
    if (!latestLogTimestamp || !metrics?.fetchedAt) return false;
    const logTime = new Date(latestLogTimestamp).getTime();
    if (isNaN(logTime)) return false;
    const refTime = new Date(metrics.fetchedAt).getTime();
    if (isNaN(refTime)) return false;
    // If the newest log in the cluster is more than 24 hours in the past, it is historical telemetry
    const ageMs = refTime - logTime;
    return ageMs > 24 * 60 * 60 * 1000;
  }, [latestLogTimestamp, metrics]);

  // Aggregate all discovered microservices across all cluster telemetry sources
  const allObservedServices = useMemo(() => {
    const set = new Set<string>();
    if (metrics?.observedServices) {
      metrics.observedServices.forEach((s) => {
        if (s && s.trim()) set.add(s.trim());
      });
    }
    if (metrics?.recentLogs) {
      metrics.recentLogs.forEach((l) => {
        if (l.service_name?.trim()) set.add(l.service_name.trim());
      });
    }
    significantEvents.forEach((e) => {
      if (e.service_name?.trim()) set.add(e.service_name.trim());
    });
    activeIncidents.forEach((i) => {
      if (i.primary_service?.trim()) set.add(i.primary_service.trim());
    });
    return Array.from(set).sort();
  }, [metrics, significantEvents, activeIncidents]);

  // SVG Chart Geometry Calculations
  const chartData = useMemo(() => {
    const buckets = timeSeries?.buckets || [];
    const hasLogActivity = buckets.some((b) => b.doc_count > 0);

    if (buckets.length === 0 || !hasLogActivity) {
      return {
        hasData: false,
        blueLinePath: "",
        blueAreaPath: "",
        redLinePath: "",
        redAreaPath: "",
        peakTotal: 0,
        peakErrors: 0,
        peakErrorPoint: null,
        startTimeLabel: "",
        endTimeLabel: "",
        peakTimeLabel: "",
      };
    }

    let maxVal = 1;
    let peakTotal = 0;
    let peakErrors = 0;
    let peakErrorIndex = -1;

    buckets.forEach((b, i) => {
      const total = b.doc_count;
      const errors = (b.severities["ERROR"] || 0) + (b.severities["FATAL"] || 0) + (b.severities["CRITICAL"] || 0);
      if (total > maxVal) maxVal = total;
      if (total > peakTotal) peakTotal = total;
      if (errors > peakErrors) {
        peakErrors = errors;
        peakErrorIndex = i;
      }
    });

    const width = 600;
    const baseY = 145;
    const topY = 25;
    const availableHeight = baseY - topY;

    const bluePoints = buckets.map((b, i) => {
      const x = buckets.length > 1 ? (i / (buckets.length - 1)) * width : width / 2;
      const y = baseY - (b.doc_count / maxVal) * availableHeight;
      return { x, y };
    });

    const redPoints = buckets.map((b, i) => {
      const errors = (b.severities["ERROR"] || 0) + (b.severities["FATAL"] || 0) + (b.severities["CRITICAL"] || 0);
      const x = buckets.length > 1 ? (i / (buckets.length - 1)) * width : width / 2;
      const y = baseY - (errors / maxVal) * availableHeight;
      return { x, y };
    });

    const blueLinePath = buildSvgPath(bluePoints);
    const blueAreaPath =
      bluePoints.length > 0
        ? `${blueLinePath} L ${bluePoints[bluePoints.length - 1].x} ${baseY} L ${bluePoints[0].x} ${baseY} Z`
        : "";

    const redLinePath = buildSvgPath(redPoints);
    const redAreaPath =
      redPoints.length > 0
        ? `${redLinePath} L ${redPoints[redPoints.length - 1].x} ${baseY} L ${redPoints[0].x} ${baseY} Z`
        : "";

    const peakErrorPoint =
      peakErrorIndex >= 0 && peakErrors > 0 ? redPoints[peakErrorIndex] : null;

    return {
      hasData: true,
      blueLinePath,
      blueAreaPath,
      redLinePath,
      redAreaPath,
      peakTotal,
      peakErrors,
      peakErrorPoint,
      startTimeLabel: formatShortTime(buckets[0]?.timestamp),
      endTimeLabel: formatShortTime(buckets[buckets.length - 1]?.timestamp),
      peakTimeLabel: peakErrorIndex >= 0 ? formatShortTime(buckets[peakErrorIndex]?.timestamp) : "",
    };
  }, [timeSeries]);

  return (
    <div className="flex flex-col w-full max-w-full min-w-0 p-4 lg:p-6 gap-5 overflow-hidden">
      {/* Top Command & Status Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-1">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-[22px] font-semibold text-[#1B1A1A] tracking-tight">
              Dashboard
            </h1>
            {readinessStatus === "ready" ? (
              isHistoricalData ? (
                <span
                  className="px-2 py-0.5 rounded font-mono text-[11px] font-medium text-[#4D80FC] bg-[#EEF3FF] border border-[#BFDBFE] flex items-center gap-1.5"
                  title="Cluster is online and connected. Telemetry represents indexed historical benchmark dataset (Oct 10–14, 2026)."
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-[#4D80FC]" />
                  <span>HISTORICAL DATASET</span>
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded font-mono text-[11px] font-semibold text-[#4D80FC] bg-[#EEF3FF] border border-[#BFDBFE] flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#31C48D] animate-pulse" />
                  <span>LIVE STREAM</span>
                </span>
              )
            ) : readinessStatus === "degraded" ? (
              <span className="px-2 py-0.5 rounded font-mono text-[11px] font-semibold text-[#B77900] bg-[#FEF7E6] border border-[#FDE8B3] flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#B77900]" />
                <span>DEGRADED</span>
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded font-mono text-[11px] font-semibold text-[#E63316] bg-[#FEECEB] border border-[#FCD3CE] flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
                <span>OFFLINE</span>
              </span>
            )}
          </div>
          <p className="text-[13px] text-[#606366]">
            System telemetry overview, active incident triage, and indexed log activity trends
            {isHistoricalData && " (Oct 2026 Benchmark)"}
          </p>
        </div>

        {/* Informational Scope Indicators (Refined flat typography) */}
        <div className="flex items-center gap-2.5 text-[12px] text-[#606366] shrink-0 self-start md:self-auto flex-wrap">
          <div className="flex items-center gap-1.5" title="Active telemetry aggregation scope">
            <Icon name="schedule" size={15} className="text-[#9B9FA3]" />
            <span>Scope: <span className="font-mono text-[#1B1A1A] font-medium">{GRANULARITY_CONFIGS[selectedGranularity].label}</span></span>
          </div>
          <span className="text-[#EDEFF3] hidden xl:inline">•</span>
          <a
            href="#service-matrix"
            className="hidden xl:flex items-center gap-1.5 font-medium hover:text-[#4D80FC] transition-colors"
            title="Jump to Service Health Matrix"
          >
            <Icon name="dns" size={15} className="text-[#9B9FA3]" />
            <span>{allObservedServices.length} Services Observed</span>
          </a>
        </div>
      </div>

      {/* Global Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-start justify-between gap-3 text-[#E63316]">
          <div className="flex items-start gap-2.5">
            <Icon name="error" size={20} className="shrink-0 mt-0.5" />
            <div className="flex flex-col gap-0.5">
              <span className="font-semibold text-[13px]">
                Unable to load live telemetry
              </span>
              <p className="text-[12px] text-[#606366]">{error}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleRefresh}
            className="px-3 py-1 bg-white border border-[#FCD3CE] rounded-lg text-[12px] font-medium text-[#E63316] hover:bg-[#FEECEB] transition-colors shrink-0 cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}

      {/* KPI Row: 4 Metric Cards (Clean typography, no boxy pill wrapping) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 min-w-0 w-full">
        {/* Card 1: Active Incidents */}
        <Link
          href="/incidents"
          className="flex flex-col justify-between p-4 rounded-xl bg-white border border-[#EDEFF3] min-h-[120px] min-w-0 hover:border-[#4D80FC]/50 transition-colors group"
          title="Open Incident Catalogue"
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-[#9B9FA3] uppercase tracking-wider group-hover:text-[#4D80FC] transition-colors">
              Active Incidents
            </span>
            <Icon
              name="warning"
              size={17}
              className={`transition-colors ${
                incidentError
                  ? "text-[#E63316]"
                  : activeIncidentsCount && activeIncidentsCount > 0
                  ? "text-[#B77900]"
                  : "text-[#9B9FA3] group-hover:text-[#4D80FC]"
              }`}
            />
          </div>
          <div className="flex items-baseline gap-2.5 my-1.5">
            {isLoadingIncidents ? (
              <span className="inline-block w-12 h-8 bg-[#EDEFF3] rounded animate-pulse" />
            ) : incidentError ? (
              <span className="text-[30px] text-[#9B9FA3] font-semibold tracking-tight font-mono leading-none">
                —
              </span>
            ) : (
              <span className="text-[30px] text-[#1B1A1A] font-semibold tracking-tight font-mono leading-none">
                {activeIncidentsCount !== null
                  ? activeIncidentsCount.toString().padStart(2, "0")
                  : "00"}
              </span>
            )}
            {incidentError ? (
              <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#E63316]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
                Unavailable
              </span>
            ) : activeIncidentsCount && activeIncidentsCount > 0 ? (
              <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#B77900]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#B77900]" />
                Under Triage
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#057A55]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#31C48D]" />
                All Clear
              </span>
            )}
          </div>
          <div className="text-[12px] text-[#606366] leading-tight">
            {incidentError ? (
              <span className="text-[#E63316]">Catalogue unavailable — click to inspect</span>
            ) : activeIncidentsCount && activeIncidentsCount > 0 ? (
              <span>{activeIncidentsCount} incident(s) currently under investigation</span>
            ) : (
              <span>No active incidents requiring triage</span>
            )}
          </div>
        </Link>

        {/* Card 2: Log Events */}
        <Link
          href="/logs"
          className="flex flex-col justify-between p-4 rounded-xl bg-white border border-[#EDEFF3] min-h-[120px] min-w-0 hover:border-[#4D80FC]/50 transition-colors group"
          title="Open Log Explorer"
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-[#9B9FA3] uppercase tracking-wider group-hover:text-[#4D80FC] transition-colors">
              Indexed Logs
            </span>
            <Icon name="database" size={17} className="text-[#4D80FC]" />
          </div>
          <div className="flex items-baseline gap-2.5 my-1.5">
            {isLoading ? (
              <span className="inline-block w-16 h-8 bg-[#EDEFF3] rounded animate-pulse" />
            ) : (
              <span className="text-[30px] text-[#1B1A1A] font-semibold tracking-tight font-mono leading-none">
                {metrics ? metrics.totalLogs.toLocaleString() : "0"}
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#4D80FC]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#4D80FC]" />
              OpenSearch
            </span>
          </div>
          <div className="text-[12px] text-[#606366] leading-tight">
            Total indexed log records in cluster
          </div>
        </Link>

        {/* Card 3: Raw Error & Fatal Logs */}
        <Link
          href="/logs?errors_only=true"
          className="flex flex-col justify-between p-4 rounded-xl bg-white border border-[#EDEFF3] min-h-[120px] min-w-0 hover:border-[#E63316]/50 transition-colors group"
          title="Filter Error and Fatal Logs"
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-[#9B9FA3] uppercase tracking-wider group-hover:text-[#E63316] transition-colors">
              Raw Error Logs
            </span>
            <Icon name="error" size={17} className="text-[#E63316]" />
          </div>
          <div className="flex items-baseline gap-2.5 my-1.5">
            {isLoading ? (
              <span className="inline-block w-12 h-8 bg-[#EDEFF3] rounded animate-pulse" />
            ) : (
              <span className="text-[30px] text-[#1B1A1A] font-semibold tracking-tight font-mono leading-none">
                {metrics ? metrics.totalErrors.toLocaleString() : "0"}
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#E63316]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
              ERROR / FATAL
            </span>
          </div>
          <div className="text-[12px] text-[#606366] leading-tight" title="Individual log lines with error severity.">
            {metrics ? `${metrics.totalErrors} error lines (${metrics.totalWarnings} warnings)` : "Raw error log count"}
          </div>
        </Link>

        {/* Card 4: Services Observed */}
        <div className="flex flex-col justify-between p-4 rounded-xl bg-white border border-[#EDEFF3] min-h-[120px] min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-[#9B9FA3] uppercase tracking-wider">
              Services Observed
            </span>
            <Icon name="hub" size={17} className="text-[#4D80FC]" />
          </div>
          <div className="flex items-baseline gap-2.5 my-1.5">
            {isLoading ? (
              <span className="inline-block w-12 h-8 bg-[#EDEFF3] rounded animate-pulse" />
            ) : (
              <span className="text-[30px] text-[#1B1A1A] font-semibold tracking-tight font-mono leading-none">
                {allObservedServices.length.toString().padStart(2, "0")}
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[#057A55]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#31C48D]" />
              Discovered
            </span>
          </div>
          <div className="text-[12px] text-[#606366] leading-tight">
            Active microservices emitting telemetry
          </div>
        </div>
      </div>

      {/* Primary Responsive Split Grid (7 cols primary, 5 cols secondary) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start min-w-0 w-full">
        {/* Left Column: Observability Trends & Health Matrix (7 cols / ~58%) */}
        <div className="lg:col-span-7 flex flex-col gap-5 min-w-0 w-full">
          {/* Error & Activity Trend Chart Panel */}
          <div className="flex flex-col p-4 sm:p-5 rounded-xl bg-white border border-[#EDEFF3] gap-3 min-w-0 w-full">
            <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-3">
              <div className="flex flex-col">
                <div className="flex items-center gap-2">
                  <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                    Error &amp; Activity Trend
                  </h2>
                  <span className="font-mono text-[11px] text-[#4D80FC] font-medium">
                    Telemetry Stream
                  </span>
                </div>
                <span className="font-mono text-[11px] text-[#9B9FA3]">
                  OpenSearch date_histogram buckets ({GRANULARITY_CONFIGS[selectedGranularity].interval} interval)
                </span>
              </div>
              {/* Granularity Tabs */}
              <div className="flex items-center p-0.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] gap-0.5 self-start xl:self-auto">
                {(["1H", "6H", "24H", "7D", "ALL"] as GranularityPreset[]).map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => handleGranularityChange(preset)}
                    className={`px-2.5 py-1 rounded font-mono text-[11px] transition-colors cursor-pointer ${
                      selectedGranularity === preset
                        ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold shadow-2xs"
                        : "text-[#606366] hover:text-[#1B1A1A] font-medium"
                    }`}
                  >
                    {preset === "ALL" ? "All Time" : preset}
                  </button>
                ))}
              </div>
            </div>

            {/* Dynamic SVG Metric Stream Chart */}
            <div className="w-full h-56 pt-2 relative rounded-lg bg-[#FDFDFE] border border-[#EDEFF3] overflow-hidden flex flex-col justify-end min-w-0">
              {isLoadingChart || (isLoading && !timeSeries) ? (
                <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-20">
                  <div className="flex items-center gap-2 font-mono text-[12px] text-[#606366]">
                    <Icon name="refresh" size={16} className="animate-spin text-[#4D80FC]" />
                    <span>Aggregating time buckets...</span>
                  </div>
                </div>
              ) : error ? (
                <div className="absolute inset-0 flex items-center justify-center p-4 text-center z-20">
                  <div className="flex flex-col items-center gap-1 text-[#E63316]">
                    <Icon name="error" size={20} />
                    <span className="text-[12px] font-medium">{error}</span>
                  </div>
                </div>
              ) : !chartData.hasData || (timeSeries && timeSeries.total_logs === 0) ? (
                <div className="absolute inset-0 flex items-center justify-center p-6 text-center z-20 bg-white/95">
                  <div className="flex flex-col items-center gap-2 max-w-sm">
                    <div className="w-10 h-10 rounded-full bg-[#F5F6F9] border border-[#EDEFF3] flex items-center justify-center text-[#9B9FA3]">
                      <Icon name="query_stats" size={20} />
                    </div>
                    <span className="text-[13px] font-semibold text-[#1B1A1A]">
                      No log volume recorded in {GRANULARITY_CONFIGS[selectedGranularity].label}
                    </span>
                    <p className="text-[11px] text-[#606366] leading-relaxed">
                      {metrics && metrics.totalLogs > 0
                        ? `${metrics.totalLogs} indexed log events exist in OpenSearch outside this sliding window (most recent on ${formatShortTime(metrics.recentLogs[0]?.timestamp)}).`
                        : "No logs found in the cluster. Upload logs via Log Upload to view live aggregation curves."}
                    </p>
                    {metrics && metrics.totalLogs > 0 && selectedGranularity !== "ALL" && (
                      <button
                        type="button"
                        onClick={() => handleGranularityChange("ALL")}
                        className="mt-1 px-3 py-1.5 rounded-lg bg-[#EEF3FF] hover:bg-[#E0EAFF] border border-[#BFDBFE] text-[#4D80FC] text-[11px] font-semibold transition-colors cursor-pointer flex items-center gap-1.5"
                      >
                        <Icon name="history" size={14} />
                        <span>Switch to All Time ({metrics.totalLogs} Logs)</span>
                      </button>
                    )}
                  </div>
                </div>
              ) : null}

              {/* Crisp SVG Graph */}
              <svg
                className="w-full h-full block"
                preserveAspectRatio="none"
                viewBox="0 0 600 160"
              >
                <defs>
                  <linearGradient id="liveErrorGrad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#E63316" stopOpacity="0.22" />
                    <stop offset="100%" stopColor="#E63316" stopOpacity="0.0" />
                  </linearGradient>
                  <linearGradient id="liveBlueGrad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#4D80FC" stopOpacity="0.20" />
                    <stop offset="100%" stopColor="#4D80FC" stopOpacity="0.0" />
                  </linearGradient>
                </defs>

                {/* Grid Lines (#EDEFF3) */}
                <line stroke="#EDEFF3" strokeDasharray="3,3" strokeWidth="1" x1="0" x2="600" y1="25" y2="25" />
                <line stroke="#EDEFF3" strokeDasharray="3,3" strokeWidth="1" x1="0" x2="600" y1="65" y2="65" />
                <line stroke="#EDEFF3" strokeDasharray="3,3" strokeWidth="1" x1="0" x2="600" y1="105" y2="105" />
                <line stroke="#EDEFF3" strokeWidth="1.5" x1="0" x2="600" y1="145" y2="145" />

                {/* Total Activity Area & Curve (#4D80FC) */}
                {chartData.blueAreaPath && (
                  <path d={chartData.blueAreaPath} fill="url(#liveBlueGrad)" />
                )}
                {chartData.blueLinePath && (
                  <path
                    d={chartData.blueLinePath}
                    fill="none"
                    stroke="#4D80FC"
                    strokeLinecap="round"
                    strokeWidth="2.5"
                  />
                )}

                {/* Error Spikes Area & Curve (#E63316) */}
                {chartData.redAreaPath && (
                  <path d={chartData.redAreaPath} fill="url(#liveErrorGrad)" />
                )}
                {chartData.redLinePath && (
                  <path
                    d={chartData.redLinePath}
                    fill="none"
                    stroke="#E63316"
                    strokeLinecap="round"
                    strokeWidth="2.5"
                  />
                )}

                {/* Peak Error Marker */}
                {chartData.peakErrorPoint && (
                  <>
                    <circle
                      cx={chartData.peakErrorPoint.x}
                      cy={chartData.peakErrorPoint.y}
                      fill="#E63316"
                      r="4.5"
                      stroke="#ffffff"
                      strokeWidth="2"
                    />
                    <line
                      opacity="0.7"
                      stroke="#E63316"
                      strokeDasharray="2,2"
                      strokeWidth="1.5"
                      x1={chartData.peakErrorPoint.x}
                      x2={chartData.peakErrorPoint.x}
                      y1={chartData.peakErrorPoint.y}
                      y2="145"
                    />
                  </>
                )}
              </svg>
            </div>

            {/* Time Axis Labels */}
            {chartData.hasData && (
              <div className="flex items-center justify-between px-1 font-mono text-[10px] text-[#9B9FA3]">
                <span>{chartData.startTimeLabel}</span>
                {chartData.peakErrorPoint && (
                  <span className="text-[#E63316] font-medium hidden sm:inline">
                    Peak: {chartData.peakErrors} errors ({chartData.peakTimeLabel})
                  </span>
                )}
                <span>{chartData.endTimeLabel}</span>
              </div>
            )}

            {/* Technical Legend and Live Peak Indicators */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pt-2 border-t border-[#EDEFF3] font-mono text-[11px] text-[#606366]">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-1 bg-[#E63316] rounded-full" />
                  <span className="text-[#1B1A1A]">
                    Errors &amp; Fatal{" "}
                    <span className="text-[#E63316] font-medium">
                      ({chartData.peakErrors} peak/bucket)
                    </span>
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-1 bg-[#4D80FC] rounded-full" />
                  <span>
                    Total Volume{" "}
                    <span className="text-[#4D80FC] font-medium">
                      ({chartData.peakTotal} peak/bucket)
                    </span>
                  </span>
                </div>
              </div>
              <span className="text-[#9B9FA3]">
                {timeSeries?.buckets ? `${timeSeries.buckets.length} contiguous buckets (${timeSeries.total_logs} logs)` : "Active window"}
              </span>
            </div>
          </div>

          {/* Service Health Matrix */}
          <div id="service-matrix" className="flex flex-col p-4 sm:p-5 rounded-xl bg-white border border-[#EDEFF3] gap-3 min-w-0 max-w-full overflow-hidden">
            <div className="flex items-center justify-between pb-1">
              <div className="flex items-center gap-2">
                <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                  Service Health Matrix
                </h2>
                <span className="font-mono text-[11px] text-[#9B9FA3]">
                  {clusterName}
                </span>
              </div>
              <Link
                href="/logs"
                className="text-[12px] text-[#4D80FC] hover:text-[#3B6EE8] font-medium flex items-center gap-1"
              >
                <span>Explore all logs</span>
                <Icon name="arrow_forward" size={14} />
              </Link>
            </div>

            <p className="text-[12px] text-[#606366]">
              Microservices detected across indexed log telemetry. Click any service to inspect its filtered logs.
            </p>

            <div className="overflow-x-auto w-full max-w-full">
              <table className="w-full min-w-[480px] text-left text-[13px]">
                <thead>
                  <tr className="text-[11px] font-semibold text-[#9B9FA3] uppercase tracking-wider bg-[#F5F6F9] border-y border-[#EDEFF3]">
                    <th className="py-2.5 px-3">Service</th>
                    <th className="py-2.5 px-3">Stream Status</th>
                    <th className="py-2.5 px-3">Telemetry Activity</th>
                    <th className="py-2.5 px-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#EDEFF3] font-mono text-[12px]">
                  {isLoading ? (
                    <tr>
                      <td colSpan={4} className="py-6 text-center text-[#9B9FA3] font-sans">
                        Loading observed services...
                      </td>
                    </tr>
                  ) : allObservedServices.length > 0 ? (
                    allObservedServices.map((serviceName) => {
                      const hasCritical =
                        metrics?.recentLogs.some(
                          (l) => l.service_name === serviceName && (l.log_level === "FATAL" || l.log_level === "CRITICAL")
                        ) ||
                        significantEvents.some(
                          (e) => e.service_name === serviceName && (e.severity === "CRITICAL" || e.description.toLowerCase().includes("fatal"))
                        );

                      const hasError =
                        hasCritical ||
                        metrics?.recentLogs.some(
                          (l) => l.service_name === serviceName && l.log_level === "ERROR"
                        ) ||
                        significantEvents.some(
                          (e) => e.service_name === serviceName && (e.severity === "HIGH" || e.description.toLowerCase().includes("error"))
                        );

                      const hasWarn =
                        metrics?.recentLogs.some(
                          (l) => l.service_name === serviceName && (l.log_level === "WARN" || l.log_level === "WARNING")
                        );

                      return (
                        <tr key={serviceName} className="hover:bg-[#F5F6F9] transition-colors group">
                          <td className="py-3 px-3">
                            <div className="flex items-center gap-2">
                              <Icon name="dns" size={15} className="text-[#9B9FA3]" />
                              <span className="text-[#1B1A1A] font-medium font-mono text-[12px]">
                                {serviceName}
                              </span>
                            </div>
                          </td>
                          <td className="py-3 px-3">
                            {hasCritical ? (
                              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[#E63316]">
                                <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
                                Critical Burst
                              </span>
                            ) : hasError ? (
                              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[#E63316]">
                                <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
                                Error Observed
                              </span>
                            ) : hasWarn ? (
                              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[#B77900]">
                                <span className="w-1.5 h-1.5 rounded-full bg-[#B77900]" />
                                Warning Observed
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-[#057A55]">
                                <span className="w-1.5 h-1.5 rounded-full bg-[#31C48D]" />
                                Active
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-3 text-[#606366]">
                            Indexed in OpenSearch
                          </td>
                          <td className="py-3 px-3 text-right">
                            <Link
                              href={`/logs?service=${encodeURIComponent(serviceName)}`}
                              className="text-[#4D80FC] hover:text-[#3B6EE8] font-medium text-[11px] hover:underline"
                            >
                              Filter logs
                            </Link>
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td colSpan={4} className="py-8 text-center text-[#9B9FA3] font-sans">
                        No services observed yet. Upload a log file to begin telemetry streaming.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Right Column: Significant Events, Active Incidents, Diagnostic Log Stream (5 cols / ~42%) */}
        <div className="lg:col-span-5 flex flex-col gap-5 min-w-0 w-full">
          {/* Quick Action Commands (Consistent button hierarchy) */}
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-2.5">
            <Link
              href="/logs/upload"
              className="h-10 rounded-lg bg-white hover:bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] text-[13px] font-medium flex items-center justify-center gap-2 transition-colors shadow-2xs"
            >
              <Icon name="upload_file" size={16} className="text-[#606366]" />
              <span>Upload</span>
            </Link>
            <Link
              href="/logs"
              className="h-10 rounded-lg bg-white hover:bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] text-[13px] font-medium flex items-center justify-center gap-2 transition-colors shadow-2xs"
            >
              <Icon name="manage_search" size={16} className="text-[#606366]" />
              <span>Explore</span>
            </Link>
            <Link
              href="/incidents"
              className="h-10 rounded-lg bg-white hover:bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] text-[13px] font-medium flex items-center justify-center gap-2 transition-colors shadow-2xs"
            >
              <Icon name="warning" size={16} className="text-[#B77900]" />
              <span>Incidents</span>
            </Link>
            <Link
              href="/ai-investigation"
              className="h-10 rounded-lg bg-[#4D80FC] hover:bg-[#3B6EE8] border border-[#3B6EE8] text-white text-[13px] font-medium flex items-center justify-center gap-2 transition-colors shadow-xs"
            >
              <Icon name="psychology" size={16} />
              <span>Investigate</span>
            </Link>
          </div>

          {/* Significant Events Feed (Clean list items with dividers, bounded scroll) */}
          <div className="flex flex-col p-4 sm:p-5 rounded-xl bg-white border border-[#EDEFF3] gap-3 min-w-0 max-w-full overflow-hidden">
            <div className="flex items-center justify-between pb-1">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#E63316]" />
                <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                  Detected Significant Events
                </h2>
              </div>
              <span className="font-mono text-[11px] text-[#4D80FC] font-medium">
                {significantEvents.length} detected
              </span>
            </div>

            <p className="text-[12px] text-[#606366] leading-relaxed">
              Contextual error bursts grouped by service and 15-minute window. Co-occurring failures are correlated into actionable patterns.
            </p>

            <div className="flex flex-col divide-y divide-[#EDEFF3] max-h-[340px] overflow-y-auto custom-scrollbar pr-1">
              {isLoading ? (
                <div className="py-6 text-center text-[#9B9FA3] font-sans text-[12px]">
                  Evaluating detection rules...
                </div>
              ) : significantEvents.length > 0 ? (
                significantEvents.map((evt) => (
                  <div
                    key={evt.event_id}
                    className="flex flex-col py-3 first:pt-1 last:pb-1 gap-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="font-mono text-[12px] font-semibold text-[#1B1A1A] truncate">
                          {evt.service_name}
                        </span>
                        {evt.severity === "CRITICAL" ? (
                          <span className="inline-flex items-center gap-1 font-mono text-[10px] text-[#E63316] font-medium shrink-0">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#E63316]" />
                            CRITICAL
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 font-mono text-[10px] text-[#B77900] font-medium shrink-0">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#B77900]" />
                            HIGH
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[10px] text-[#9B9FA3] shrink-0 whitespace-nowrap">
                        {formatShortTime(evt.window_start).slice(0, -4)}
                      </span>
                    </div>

                    <p className="text-[12px] text-[#1B1A1A] font-medium leading-snug">
                      {evt.description}
                    </p>

                    {/* Evidence link */}
                    <div className="flex items-center justify-between pt-0.5 text-[11px] font-mono text-[#9B9FA3]">
                      <span>Evidence: {evt.evidence.log_ids.length} record(s)</span>
                      <div className="flex items-center gap-2">
                        {evt.evidence.log_ids.slice(0, 2).map((logId) => (
                          <Link
                            key={logId}
                            href={`/logs?q=${encodeURIComponent(logId)}`}
                            title={`Inspect evidence log in Log Explorer`}
                            className="text-[#4D80FC] hover:underline flex items-center gap-0.5"
                          >
                            <span>#{logId.slice(-6)}</span>
                            <Icon name="arrow_forward" size={11} />
                          </Link>
                        ))}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="p-4 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-center text-[#606366] text-[12px]">
                  No significant error bursts or fatal logs detected in active window.
                </div>
              )}
            </div>

            {significantEvents.length > 0 && (
              <div className="flex items-center justify-between pt-2 border-t border-[#EDEFF3] text-[11px] font-mono text-[#9B9FA3]">
                <span>Showing {significantEvents.length} event clusters</span>
                <span>Scroll to inspect</span>
              </div>
            )}
          </div>

          {/* Active Incidents Panel (Clean list with dividers) */}
          <div className="flex flex-col p-4 sm:p-5 rounded-xl bg-white border border-[#EDEFF3] gap-3 min-w-0 max-w-full overflow-hidden">
            <div className="flex items-center justify-between pb-1">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${activeIncidentsCount && activeIncidentsCount > 0 ? "bg-[#B77900]" : "bg-[#2E7D32]"}`} />
                <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                  Active Incidents
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-[#9B9FA3]">
                  {activeIncidentsCount !== null ? `${activeIncidentsCount} active` : "—"}
                </span>
                <Link
                  href="/incidents"
                  className="text-[12px] text-[#4D80FC] hover:text-[#3B6EE8] font-medium flex items-center gap-0.5"
                >
                  <span>Catalogue</span>
                  <Icon name="arrow_forward" size={13} />
                </Link>
              </div>
            </div>

            <p className="text-[12px] text-[#606366] leading-relaxed">
              Correlated multi-service incidents currently under active investigation or triage.
            </p>

            <div className="flex flex-col divide-y divide-[#EDEFF3]">
              {isLoadingIncidents ? (
                <div className="py-6 text-center text-[#9B9FA3] font-sans text-[12px]">
                  Loading incident catalogue...
                </div>
              ) : incidentError ? (
                <div className="p-3 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] text-[#E63316] text-[12px]">
                  {incidentError}
                </div>
              ) : activeIncidents.length > 0 ? (
                activeIncidents.map((inc) => (
                  <Link
                    key={inc.id}
                    href={`/incidents/${inc.id}`}
                    className="flex flex-col py-3 first:pt-1 last:pb-1 gap-1.5 group"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="font-mono text-[12px] font-semibold text-[#1B1A1A] truncate">
                          {inc.primary_service || "system"}
                        </span>
                        <span className="inline-flex items-center gap-1 font-mono text-[10px] text-[#B77900] font-medium shrink-0">
                          <span className="w-1.5 h-1.5 rounded-full bg-[#B77900]" />
                          {inc.severity}
                        </span>
                      </div>
                      <span className="font-mono text-[10px] text-[#4D80FC] font-medium shrink-0">
                        {inc.status}
                      </span>
                    </div>

                    <p className="text-[12px] text-[#1B1A1A] font-medium leading-snug group-hover:text-[#4D80FC] transition-colors">
                      {inc.title}
                    </p>

                    <div className="flex items-center justify-between text-[11px] font-mono text-[#9B9FA3]">
                      <span>Started: {formatShortTime(inc.started_at)}</span>
                      <span className="text-[#4D80FC] group-hover:underline flex items-center gap-0.5 font-sans font-medium text-[11px]">
                        <span>Inspect</span>
                        <Icon name="arrow_forward" size={12} />
                      </span>
                    </div>
                  </Link>
                ))
              ) : (
                <div className="flex flex-col items-center justify-center p-5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-center gap-1.5">
                  <Icon name="check_circle" size={18} className="text-[#2E7D32]" />
                  <span className="text-[12px] font-semibold text-[#1B1A1A]">
                    No Active Incidents
                  </span>
                  <p className="text-[11px] text-[#606366] max-w-xs leading-relaxed">
                    All observed services are operating within normal thresholds. New incidents will appear here when error cascades are correlated.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Diagnostic Event Stream (Clean list with dividers) */}
          <div className="flex flex-col p-4 sm:p-5 rounded-xl bg-white border border-[#EDEFF3] gap-3 min-w-0 max-w-full overflow-hidden">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#4D80FC]" />
                <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                  {isHistoricalData ? "Diagnostic Event Stream" : "Live Diagnostic Stream"}
                </h2>
              </div>
              <span className="font-mono text-[11px] text-[#9B9FA3]">
                tail -n 5 stream
              </span>
            </div>

            <div className="flex flex-col divide-y divide-[#EDEFF3] font-mono text-[12px]">
              {isLoading ? (
                <div className="py-8 text-center text-[#9B9FA3] font-sans">
                  Loading recent logs...
                </div>
              ) : metrics && metrics.recentLogs.length > 0 ? (
                metrics.recentLogs.map((log) => {
                  const isError = log.log_level === "ERROR" || log.log_level === "FATAL" || log.log_level === "CRITICAL";
                  const isWarn = log.log_level === "WARN" || log.log_level === "WARNING";

                  return (
                    <Link
                      key={log.id}
                      href={`/logs?q=${encodeURIComponent(log.id)}`}
                      className="flex flex-col gap-0.5 py-2.5 first:pt-1 last:pb-1 group cursor-pointer"
                    >
                      <div className="flex items-center justify-between text-[#9B9FA3] text-[11px]">
                        <span>{formatShortTime(log.timestamp)}</span>
                        {isError ? (
                          <span className="text-[#E63316] font-semibold">
                            [{log.log_level || "ERROR"}]
                          </span>
                        ) : isWarn ? (
                          <span className="text-[#B77900] font-semibold">
                            [{log.log_level || "WARN"}]
                          </span>
                        ) : (
                          <span className="text-[#4D80FC] font-semibold">
                            [{log.log_level || "INFO"}]
                          </span>
                        )}
                      </div>
                      <div className="text-[#606366] text-[12px] truncate group-hover:text-[#1B1A1A]">
                        <span className="text-[#1B1A1A] font-semibold font-mono">
                          {log.service_name || "system"}:
                        </span>{" "}
                        {log.message || "No message body"}
                      </div>
                    </Link>
                  );
                })
              ) : (
                <div className="py-8 text-center text-[#9B9FA3] font-sans">
                  No indexed logs found. Upload a log file to start telemetry.
                </div>
              )}
            </div>

            {/* Quick Terminal Link */}
            <div className="flex items-center justify-between pt-2 border-t border-[#EDEFF3]">
              <span className="font-mono text-[11px] text-[#9B9FA3]">
                {metrics ? `Showing ${metrics.recentLogs.length} recent events` : "OpenSearch stream"}
              </span>
              <Link
                href="/logs"
                className="font-mono text-[12px] text-[#4D80FC] hover:text-[#3B6EE8] font-medium hover:underline flex items-center gap-1"
              >
                <span>Full Log Explorer</span>
                <Icon name="open_in_new" size={14} />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
