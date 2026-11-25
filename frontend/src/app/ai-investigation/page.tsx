"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  fetchIncidents,
  IncidentSummaryItem,
} from "@/lib/api/incidents";
import { ApiError } from "@/lib/api/logs";

function getSeverityVariant(severity: string): StatusVariant {
  switch (severity?.toUpperCase()) {
    case "CRITICAL":
      return "critical";
    case "HIGH":
      return "warning";
    case "MEDIUM":
      return "info";
    case "LOW":
      return "healthy";
    default:
      return "neutral";
  }
}

function getStatusVariant(status: string): StatusVariant {
  switch (status?.toUpperCase()) {
    case "INVESTIGATING":
      return "info";
    case "DETECTED":
      return "warning";
    case "MITIGATED":
      return "healthy";
    case "RESOLVED":
      return "healthy";
    case "FALSE_POSITIVE":
      return "neutral";
    default:
      return "neutral";
  }
}

function formatUtc(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  } catch {
    return dateStr;
  }
}

export default function AiInvestigationPage() {
  const [incidents, setIncidents] = useState<IncidentSummaryItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters & search
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const loadIncidents = useCallback(async (isManualRefresh: boolean = false) => {
    if (isManualRefresh) {
      window.dispatchEvent(
        new CustomEvent("rootlens:refresh-status", { detail: { isRefreshing: true } })
      );
    } else {
      setIsLoading(true);
    }
    setError(null);

    try {
      const data = await fetchIncidents({
        severity: severityFilter || undefined,
        status: statusFilter || undefined,
        page_size: 50,
      });
      setIncidents(data.items || []);
      setTotalCount(data.total || 0);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to load incidents for investigation.");
      }
    } finally {
      setIsLoading(false);
      if (isManualRefresh) {
        window.dispatchEvent(
          new CustomEvent("rootlens:refresh-status", { detail: { isRefreshing: false } })
        );
      }
    }
  }, [severityFilter, statusFilter]);

  useEffect(() => {
    let cancelled = false;

    async function execute() {
      await Promise.resolve();
      if (cancelled) return;
      loadIncidents(false);
    }

    execute();

    return () => {
      cancelled = true;
    };
  }, [loadIncidents]);

  // Sync with global header refresh event
  useEffect(() => {
    const handleGlobalRefresh = () => {
      loadIncidents(true);
    };
    window.addEventListener("rootlens:refresh", handleGlobalRefresh);
    return () => {
      window.removeEventListener("rootlens:refresh", handleGlobalRefresh);
    };
  }, [loadIncidents]);

  // Filtered in-memory search
  const filteredIncidents = incidents.filter((inc) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const titleMatch = inc.title?.toLowerCase().includes(q);
    const serviceMatch = inc.primary_service?.toLowerCase().includes(q);
    const idMatch = inc.id?.toLowerCase().includes(q);
    return titleMatch || serviceMatch || idMatch;
  });

  // KPI Calculations
  const investigatingCount = incidents.filter((i) => i.status === "INVESTIGATING").length;
  const criticalCount = incidents.filter((i) => i.severity === "CRITICAL" || i.severity === "HIGH").length;
  const uniqueServices = Array.from(
    new Set(incidents.map((i) => i.primary_service).filter(Boolean))
  );

  return (
    <div className="flex flex-col w-full p-4 lg:p-6 gap-6 max-w-full overflow-hidden">
      {/* Header bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-3 border-b border-[#D8D5CD]">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[20px] sm:text-[22px] font-semibold text-[#171A18] tracking-tight">
              AI Investigation &amp; RCA Workspace
            </h1>
            <StatusBadge label="INCIDENT-GROUNDED" variant="healthy" dot />
            <StatusBadge label="CONFIRMED CAUSALITY: FALSE" variant="neutral" />
          </div>
          <p className="text-[13px] text-[#5F6360] leading-relaxed">
            Automated causal hypothesis generation, interactive natural-language SRE triage, and multi-service anomaly correlation grounded strictly in verified log telemetry.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <Link
            href="/incidents"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors"
          >
            <Icon name="receipt_long" size={16} className="text-[#5F6360]" />
            <span>Incidents Catalogue</span>
          </Link>
          <Link
            href="/logs"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors"
          >
            <Icon name="search" size={16} className="text-[#5F6360]" />
            <span>Log Explorer</span>
          </Link>
        </div>
      </div>

      {/* Operational KPI Metric Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Total Incidents
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#1F5A43]">
              <Icon name="warning" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {totalCount}
            </span>
            <span className="text-[11px] text-[#5F6360]">registered in catalogue</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Active Triage
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#EEF3FF] border border-[#BFDBFE] flex items-center justify-center text-[#2563EB]">
              <Icon name="psychology" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {investigatingCount}
            </span>
            <span className="text-[11px] text-[#2563EB] font-medium">in investigation</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Critical / High
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-center text-[#E63316]">
              <Icon name="priority_high" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {criticalCount}
            </span>
            <span className="text-[11px] text-[#5F6360]">high blast radius</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Suspect Services
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#9A7440]">
              <Icon name="hub" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {uniqueServices.length}
            </span>
            <span className="text-[11px] text-[#5F6360]">observed culprits</span>
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="p-3.5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className="relative flex-1 min-w-[200px]">
            <Icon
              name="search"
              size={17}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F6360]"
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by title, affected service, or incident ID..."
              className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] placeholder-[#5F6360] focus:outline-none focus:ring-2 focus:ring-[#1F5A43]"
            />
          </div>
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              className="text-[12px] text-[#5F6360] hover:text-[#171A18] px-2 py-1 rounded bg-[#EAE7DF]"
            >
              Clear
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <div className="flex items-center gap-1.5 text-[12px] text-[#5F6360]">
            <span>Severity:</span>
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[12px] text-[#171A18] focus:outline-none focus:ring-2 focus:ring-[#1F5A43]"
            >
              <option value="">All Severities</option>
              <option value="CRITICAL">Critical</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
          </div>

          <div className="flex items-center gap-1.5 text-[12px] text-[#5F6360]">
            <span>Status:</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[12px] text-[#171A18] focus:outline-none focus:ring-2 focus:ring-[#1F5A43]"
            >
              <option value="">All Statuses</option>
              <option value="INVESTIGATING">Investigating</option>
              <option value="DETECTED">Detected</option>
              <option value="MITIGATED">Mitigated</option>
              <option value="RESOLVED">Resolved</option>
            </select>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-between gap-3 text-[#A34B4B]">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Icon name="error" size={18} />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => loadIncidents(false)}
            className="text-[12px] font-semibold underline hover:no-underline cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}

      {/* Main Incident Investigation Ledger */}
      <div className="flex flex-col gap-3 min-w-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-[15px] font-semibold text-[#171A18]">
              Active Incident Investigation Queue
            </h2>
            <span className="text-[12px] font-mono text-[#5F6360]">
              ({filteredIncidents.length} candidate{filteredIncidents.length === 1 ? "" : "s"})
            </span>
          </div>
          <span className="font-mono text-[11px] text-[#5F6360] hidden sm:inline">
            Direct deep-links to RCA reasoning workspace
          </span>
        </div>

        {/* Loading Skeleton */}
        {isLoading && (
          <div className="flex flex-col gap-3">
            {[1, 2, 3].map((n) => (
              <div
                key={n}
                className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-3 animate-pulse"
              >
                <div className="h-4 w-48 bg-[#EAE7DF] rounded-sm" />
                <div className="h-6 w-full bg-[#EAE7DF] rounded-md" />
                <div className="h-4 w-64 bg-[#EAE7DF] rounded-sm" />
              </div>
            ))}
          </div>
        )}

        {/* Empty State */}
        {!isLoading && filteredIncidents.length === 0 && (
          <div className="p-8 sm:p-12 rounded-xl bg-white border border-[#D8D5CD] flex flex-col items-center justify-center text-center gap-3.5 shadow-xs">
            <div className="w-12 h-12 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#5F6360]">
              <Icon name="search_off" size={26} />
            </div>
            <div className="flex flex-col gap-1 max-w-md">
              <h3 className="text-[16px] font-semibold text-[#171A18]">
                {incidents.length === 0
                  ? "No Incidents Registered for Investigation"
                  : "No Incidents Match Search Criteria"}
              </h3>
              <p className="text-[13px] text-[#5F6360] leading-relaxed">
                {incidents.length === 0
                  ? "RootLens investigates incidents grounded in indexed log telemetry. Evaluate candidates from OpenSearch error spikes or upload a benchmark log dataset."
                  : "Try clearing your search query or loosening your severity and status filters."}
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-2.5 pt-2">
              {incidents.length === 0 ? (
                <>
                  <Link
                    href="/incidents"
                    className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[13px] font-medium transition-colors shadow-xs"
                  >
                    <Icon name="radar" size={16} />
                    <span>Evaluate Log Candidates</span>
                  </Link>
                  <Link
                    href="/logs/upload"
                    className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors"
                  >
                    <Icon name="upload_file" size={16} className="text-[#5F6360]" />
                    <span>Upload Telemetry Logs</span>
                  </Link>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery("");
                    setSeverityFilter("");
                    setStatusFilter("");
                  }}
                  className="px-3.5 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] font-medium text-[#171A18] hover:bg-[#EAE7DF]"
                >
                  Reset All Filters
                </button>
              )}
            </div>
          </div>
        )}

        {/* Live Incident Rows */}
        {!isLoading && filteredIncidents.length > 0 && (
          <div className="flex flex-col gap-3 min-w-0">
            {filteredIncidents.map((incident) => {
              const primarySvc = incident.primary_service || "unassigned";
              const detectedUtc = formatUtc(incident.detected_at);
              const startedUtc = formatUtc(incident.started_at);

              return (
                <div
                  key={incident.id}
                  className="p-4 sm:p-5 rounded-xl bg-white border border-[#D8D5CD] hover:border-[#1F5A43]/40 flex flex-col lg:flex-row lg:items-center justify-between gap-4 transition-all shadow-xs group"
                >
                  {/* Left Column: Title, Metadata, Severity */}
                  <div className="flex flex-col gap-2 min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge
                        label={incident.severity}
                        variant={getSeverityVariant(incident.severity)}
                      />
                      <StatusBadge
                        label={incident.status}
                        variant={getStatusVariant(incident.status)}
                        dot
                      />
                      <span className="font-mono text-[11px] text-[#5F6360] px-1.5 py-0.5 rounded bg-[#F5F3EE] border border-[#D8D5CD]">
                        ID: {incident.id.slice(0, 8)}...
                      </span>
                      <span className="text-[11px] text-[#5F6360] flex items-center gap-1">
                        <Icon name="schedule" size={14} className="text-[#5F6360]" />
                        <span>Detected {detectedUtc}</span>
                      </span>
                    </div>

                    <div className="flex flex-col gap-1">
                      <Link
                        href={`/incidents/${incident.id}?tab=investigate`}
                        className="text-[15px] font-semibold text-[#171A18] hover:text-[#1F5A43] transition-colors leading-snug line-clamp-2"
                      >
                        {incident.title}
                      </Link>
                      <div className="flex flex-wrap items-center gap-y-1 gap-x-3 text-[12px] text-[#5F6360]">
                        <span className="flex items-center gap-1 font-mono text-[#171A18] bg-[#F5F3EE] px-2 py-0.5 rounded border border-[#D8D5CD]">
                          <Icon name="lan" size={14} className="text-[#1F5A43]" />
                          <span>Primary: {primarySvc}</span>
                        </span>
                        <span>
                          Blast Radius: <strong className="text-[#171A18] font-mono">{incident.affected_service_count}</strong> service(s)
                        </span>
                        <span>•</span>
                        <span>
                          Timeline: <strong className="text-[#171A18] font-mono">{incident.event_count}</strong> milestone event(s)
                        </span>
                        <span>•</span>
                        <span>
                          Evidence: <strong className="text-[#171A18] font-mono">{incident.evidence_count}</strong> verified record(s)
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Right Column: Grounding Badges & Launch CTAs */}
                  <div className="flex flex-col sm:flex-row lg:flex-col items-start sm:items-center lg:items-end justify-between sm:justify-end gap-3 pt-3 lg:pt-0 border-t lg:border-t-0 border-[#D8D5CD] shrink-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[11px] font-mono text-[#5F6360] px-2 py-0.5 rounded bg-[#F5F3EE] border border-[#D8D5CD]">
                        Timeline: {startedUtc.split(" ")[1] || startedUtc}
                      </span>
                      <span className="text-[11px] font-mono text-[#9A7440] px-2 py-0.5 rounded bg-[#FEF7E6] border border-[#FDE8B3]">
                        Causality: Unconfirmed
                      </span>
                    </div>

                    <div className="flex items-center gap-2 w-full sm:w-auto">
                      <Link
                        href={`/incidents/${incident.id}?tab=timeline`}
                        title="View chronological timeline & evidence citations"
                        className="px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[12px] font-medium text-[#171A18] transition-colors"
                      >
                        Timeline
                      </Link>
                      <Link
                        href={`/incidents/${incident.id}?tab=overview`}
                        title="View diagnostic explanation & hypotheses"
                        className="px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[12px] font-medium text-[#171A18] transition-colors"
                      >
                        RCA Findings
                      </Link>
                      <Link
                        href={`/incidents/${incident.id}?tab=investigate`}
                        className="flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[13px] font-medium transition-colors shadow-xs group-hover:bg-[#1D4ED8]"
                      >
                        <Icon name="psychology" size={16} />
                        <span>Launch Investigation</span>
                        <Icon name="arrow_forward" size={14} />
                      </Link>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Investigation Methodology & Safety Boundary */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-3 shadow-xs">
        <div className="flex items-center justify-between border-b border-[#D8D5CD] pb-2.5">
          <div className="flex items-center gap-2">
            <Icon name="verified_user" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              RootLens Investigation Guardrails &amp; Evidence Grounding
            </h3>
          </div>
          <span className="font-mono text-[11px] text-[#5F6360]">
            Deterministic Invariant Rules
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-1">
          <div className="flex flex-col gap-1 text-[12px]">
            <span className="font-semibold text-[#171A18] flex items-center gap-1.5">
              <Icon name="timeline" size={15} className="text-[#1F5A43]" />
              <span>1. Millisecond Timeline Ordering</span>
            </span>
            <p className="text-[#5F6360] leading-relaxed">
              Traces error cascades across interdependent microservices chronologically. Earliest observed failures are flagged as initial triggers, not confirmed root causes.
            </p>
          </div>

          <div className="flex flex-col gap-1 text-[12px]">
            <span className="font-semibold text-[#171A18] flex items-center gap-1.5">
              <Icon name="fingerprint" size={15} className="text-[#1F5A43]" />
              <span>2. Verifiable Evidence Fencing</span>
            </span>
            <p className="text-[#5F6360] leading-relaxed">
              Diagnostic explanations cite verifiable OpenSearch document IDs and SHA-256 provenance hashes. Natural-language SRE inquiries strictly refuse to hallucinate unseen entities.
            </p>
          </div>

          <div className="flex flex-col gap-1 text-[12px]">
            <span className="font-semibold text-[#171A18] flex items-center gap-1.5">
              <Icon name="gavel" size={15} className="text-[#9A7440]" />
              <span>3. Human-in-the-Loop Confirmation</span>
            </span>
            <p className="text-[#5F6360] leading-relaxed">
              AI hypotheses never become confirmed root causes automatically. Engineering operators review and record confirm/reject decisions into an immutable PostgreSQL audit ledger.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
