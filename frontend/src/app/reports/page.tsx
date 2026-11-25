"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  fetchIncidents,
  fetchIncidentReports,
  generateIncidentReport,
  IncidentSummaryItem,
  ReportResponse,
} from "@/lib/api/incidents";
import { ApiError } from "@/lib/api/logs";

interface ReportWithIncident {
  report: ReportResponse;
  incident?: IncidentSummaryItem;
}

function getSeverityVariant(severity?: string): StatusVariant {
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

function getStatusVariant(status?: string): StatusVariant {
  switch (status?.toUpperCase()) {
    case "PUBLISHED":
      return "healthy";
    case "FINAL":
      return "healthy";
    case "DRAFT":
      return "info";
    case "INVESTIGATING":
      return "info";
    case "DETECTED":
      return "warning";
    case "MITIGATED":
      return "healthy";
    case "RESOLVED":
      return "healthy";
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

export default function ReportsPage() {
  const [reportsWithIncidents, setReportsWithIncidents] = useState<ReportWithIncident[]>([]);
  const [incidents, setIncidents] = useState<IncidentSummaryItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Active view tab: "compiled" | "pending"
  const [activeViewTab, setActiveViewTab] = useState<"compiled" | "pending">("compiled");

  // Filter & search
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [serviceFilter, setServiceFilter] = useState<string>("");

  // In-App Retrospective Reader State
  const [selectedReport, setSelectedReport] = useState<ReportWithIncident | null>(null);
  const [copySuccess, setCopySuccess] = useState<boolean>(false);

  // Quick generation state
  const [generatingIncidentId, setGeneratingIncidentId] = useState<string | null>(null);
  const [generationNotice, setGenerationNotice] = useState<string | null>(null);

  const loadData = useCallback(async (isManualRefresh: boolean = false) => {
    if (isManualRefresh) {
      window.dispatchEvent(
        new CustomEvent("rootlens:refresh-status", { detail: { isRefreshing: true } })
      );
    } else {
      setIsLoading(true);
    }
    setError(null);

    try {
      // 1. Fetch persisted incidents from PostgreSQL catalogue
      const incidentData = await fetchIncidents({ page_size: 50 });
      const incList = incidentData.items || [];
      setIncidents(incList);

      // 2. Query reports for each incident in parallel
      const reportPromises = incList.map(async (inc) => {
        try {
          const res = await fetchIncidentReports(inc.id);
          return (res.items || []).map((rep) => ({
            report: rep,
            incident: inc,
          }));
        } catch {
          return [] as ReportWithIncident[];
        }
      });

      const settled = await Promise.allSettled(reportPromises);
      const allReports: ReportWithIncident[] = [];
      settled.forEach((result) => {
        if (result.status === "fulfilled") {
          allReports.push(...result.value);
        }
      });

      // Sort newest report first
      allReports.sort(
        (a, b) =>
          new Date(b.report.generated_at).getTime() - new Date(a.report.generated_at).getTime()
      );

      setReportsWithIncidents(allReports);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Failed to load post-mortem reports.");
      }
    } finally {
      setIsLoading(false);
      if (isManualRefresh) {
        window.dispatchEvent(
          new CustomEvent("rootlens:refresh-status", { detail: { isRefreshing: false } })
        );
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function execute() {
      await Promise.resolve();
      if (cancelled) return;
      loadData(false);
    }

    execute();

    return () => {
      cancelled = true;
    };
  }, [loadData]);

  // Sync with global header refresh event
  useEffect(() => {
    const handleGlobalRefresh = () => {
      loadData(true);
    };
    window.addEventListener("rootlens:refresh", handleGlobalRefresh);
    return () => {
      window.removeEventListener("rootlens:refresh", handleGlobalRefresh);
    };
  }, [loadData]);

  // Quick report generation for an incident
  const handleGenerateReportForIncident = async (incidentId: string) => {
    setGeneratingIncidentId(incidentId);
    setGenerationNotice(null);
    try {
      const newReport = await generateIncidentReport(incidentId);
      setGenerationNotice(`Post-mortem generated: "${newReport.title}"`);
      await loadData(false);
      // Automatically switch to compiled view and select the newly generated report
      setActiveViewTab("compiled");
      const matched = reportsWithIncidents.find((r) => r.report.id === newReport.id);
      if (matched) {
        setSelectedReport(matched);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to compile post-mortem report.");
    } finally {
      setGeneratingIncidentId(null);
      setTimeout(() => setGenerationNotice(null), 6000);
    }
  };

  const handleCopyMarkdown = (content: string) => {
    if (!content) return;
    navigator.clipboard.writeText(content);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2500);
  };

  // Incidents that do not yet have a post-mortem report
  const reportedIncidentIds = new Set(reportsWithIncidents.map((r) => r.report.incident_id));
  const pendingIncidents = incidents.filter((inc) => !reportedIncidentIds.has(inc.id));

  // Filtered reports
  const filteredReports = reportsWithIncidents.filter((item) => {
    const q = searchQuery.toLowerCase().trim();
    const svcMatch = serviceFilter
      ? (item.incident?.primary_service || "").toLowerCase() === serviceFilter.toLowerCase()
      : true;

    if (!q) return svcMatch;

    const titleMatch = item.report.title.toLowerCase().includes(q);
    const incidentTitleMatch = item.incident?.title?.toLowerCase().includes(q);
    const idMatch = item.report.id.toLowerCase().includes(q) || item.report.incident_id.toLowerCase().includes(q);
    return svcMatch && (titleMatch || incidentTitleMatch || idMatch);
  });

  const uniqueServices = Array.from(
    new Set(
      reportsWithIncidents
        .map((r) => r.incident?.primary_service)
        .filter(Boolean) as string[]
    )
  );

  return (
    <div className="flex flex-col w-full p-4 lg:p-6 gap-6 max-w-full overflow-hidden">
      {/* Header bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-3 border-b border-[#D8D5CD]">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[20px] sm:text-[22px] font-semibold text-[#171A18] tracking-tight">
              Post-Mortems &amp; Retrospectives
            </h1>
            <StatusBadge label="POSTGRESQL AUDITED" variant="healthy" dot />
            <StatusBadge label="STATUS: DRAFT BY DEFAULT" variant="info" />
            <StatusBadge label="UNCONFIRMED CAUSALITY INVARIANT" variant="neutral" />
          </div>
          <p className="text-[13px] text-[#5F6360] leading-relaxed">
            Deterministic incident retrospectives compiling chronological failure chains, blast-radius impact, verified evidence citations, and non-autonomous remediation runbooks.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <Link
            href="/incidents"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors"
          >
            <Icon name="warning" size={16} className="text-[#5F6360]" />
            <span>Incidents Catalogue</span>
          </Link>
          <Link
            href="/ai-investigation"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors"
          >
            <Icon name="psychology" size={16} className="text-[#5F6360]" />
            <span>AI Investigation</span>
          </Link>
        </div>
      </div>

      {/* Governance Metrics Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Compiled Retrospectives
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#1F5A43]">
              <Icon name="summarize" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {reportsWithIncidents.length}
            </span>
            <span className="text-[11px] text-[#5F6360]">persisted reports</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Documented Incidents
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#EEF3FF] border border-[#BFDBFE] flex items-center justify-center text-[#2563EB]">
              <Icon name="history_edu" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {reportedIncidentIds.size}
            </span>
            <span className="text-[11px] text-[#2563EB] font-medium">with post-mortems</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Pending Retrospectives
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] flex items-center justify-center text-[#B77900]">
              <Icon name="pending_actions" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              {pendingIncidents.length}
            </span>
            <span className="text-[11px] text-[#5F6360]">awaiting compilation</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col justify-between gap-2 shadow-xs">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Advisory Compliance
            </span>
            <div className="w-7 h-7 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#9A7440]">
              <Icon name="shield" size={16} />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[22px] font-semibold text-[#171A18] font-mono">
              100%
            </span>
            <span className="text-[11px] text-[#5F6360]">advisory only</span>
          </div>
        </div>
      </div>

      {/* Generation Toast Notice */}
      {generationNotice && (
        <div className="p-3.5 rounded-xl bg-[#EDF9ED] border border-[#C5ECC4] flex items-center justify-between gap-3 text-[#2E7D32] shadow-xs">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Icon name="check_circle" size={18} />
            <span>{generationNotice}</span>
          </div>
          <button
            type="button"
            onClick={() => setGenerationNotice(null)}
            className="text-[12px] font-semibold hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-between gap-3 text-[#A34B4B]">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Icon name="error" size={18} />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => loadData(false)}
            className="text-[12px] font-semibold underline hover:no-underline cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}

      {/* Workspace Tabs: Compiled Reports vs Pending Incidents */}
      <div className="flex flex-col gap-4 min-w-0">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#D8D5CD] pb-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setActiveViewTab("compiled")}
              className={`flex items-center gap-2 px-3.5 py-2 text-[13px] rounded-lg font-medium transition-colors cursor-pointer ${
                activeViewTab === "compiled"
                  ? "bg-[#171A18] text-white shadow-xs"
                  : "bg-white text-[#5F6360] hover:text-[#171A18] hover:bg-[#EAE7DF] border border-[#D8D5CD]"
              }`}
            >
              <Icon name="article" size={16} />
              <span>Compiled Post-Mortems</span>
              <span
                className={`text-[11px] font-mono px-1.5 py-0.2 rounded-full ${
                  activeViewTab === "compiled" ? "bg-white/20 text-white" : "bg-[#F5F3EE] text-[#171A18]"
                }`}
              >
                {reportsWithIncidents.length}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setActiveViewTab("pending")}
              className={`flex items-center gap-2 px-3.5 py-2 text-[13px] rounded-lg font-medium transition-colors cursor-pointer ${
                activeViewTab === "pending"
                  ? "bg-[#171A18] text-white shadow-xs"
                  : "bg-white text-[#5F6360] hover:text-[#171A18] hover:bg-[#EAE7DF] border border-[#D8D5CD]"
              }`}
            >
              <Icon name="pending_actions" size={16} />
              <span>Pending Retrospective</span>
              <span
                className={`text-[11px] font-mono px-1.5 py-0.2 rounded-full ${
                  activeViewTab === "pending" ? "bg-white/20 text-white" : "bg-[#F5F3EE] text-[#171A18]"
                }`}
              >
                {pendingIncidents.length}
              </span>
            </button>
          </div>

          <span className="font-mono text-[11px] text-[#5F6360]">
            Deterministic synthesis from verified OpenSearch telemetry
          </span>
        </div>

        {/* Tab 1: Compiled Post-Mortems */}
        {activeViewTab === "compiled" && (
          <div className="flex flex-col gap-4">
            {/* Search and Service Filter */}
            <div className="p-3.5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
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
                  placeholder="Filter by report title, incident, or report ID..."
                  className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] placeholder-[#5F6360] focus:outline-none focus:ring-2 focus:ring-[#1F5A43]"
                />
              </div>

              {uniqueServices.length > 0 && (
                <div className="flex items-center gap-2 text-[12px] text-[#5F6360] shrink-0">
                  <span>Service:</span>
                  <select
                    value={serviceFilter}
                    onChange={(e) => setServiceFilter(e.target.value)}
                    className="px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[12px] text-[#171A18] focus:outline-none focus:ring-2 focus:ring-[#1F5A43]"
                  >
                    <option value="">All Services</option>
                    {uniqueServices.map((svc) => (
                      <option key={svc} value={svc}>
                        {svc}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            {/* Loading Skeleton */}
            {isLoading && (
              <div className="flex flex-col gap-3">
                {[1, 2].map((n) => (
                  <div
                    key={n}
                    className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-3 animate-pulse"
                  >
                    <div className="h-4 w-56 bg-[#EAE7DF] rounded-sm" />
                    <div className="h-6 w-full bg-[#EAE7DF] rounded-md" />
                    <div className="h-4 w-72 bg-[#EAE7DF] rounded-sm" />
                  </div>
                ))}
              </div>
            )}

            {/* Empty State */}
            {!isLoading && filteredReports.length === 0 && (
              <div className="p-8 sm:p-12 rounded-xl bg-white border border-[#D8D5CD] flex flex-col items-center justify-center text-center gap-3.5 shadow-xs">
                <div className="w-12 h-12 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#5F6360]">
                  <Icon name="history_edu" size={26} />
                </div>
                <div className="flex flex-col gap-1 max-w-md">
                  <h3 className="text-[16px] font-semibold text-[#171A18]">
                    {reportsWithIncidents.length === 0
                      ? "No Post-Mortem Retrospectives Compiled Yet"
                      : "No Retrospectives Match Current Filters"}
                  </h3>
                  <p className="text-[13px] text-[#5F6360] leading-relaxed">
                    {reportsWithIncidents.length === 0
                      ? "Post-mortems are deterministically generated from investigated incidents. Choose an incident from the catalogue to compile an evidence-backed retrospective."
                      : "Try loosening your search query or service filter."}
                  </p>
                </div>
                <div className="flex flex-wrap items-center justify-center gap-2 pt-2">
                  {reportsWithIncidents.length === 0 ? (
                    <button
                      type="button"
                      onClick={() => setActiveViewTab("pending")}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[13px] font-medium transition-colors shadow-xs cursor-pointer"
                    >
                      <Icon name="pending_actions" size={16} />
                      <span>View Eligible Incidents ({pendingIncidents.length})</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setSearchQuery("");
                        setServiceFilter("");
                      }}
                      className="px-3.5 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] font-medium text-[#171A18] hover:bg-[#EAE7DF]"
                    >
                      Reset Filters
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Reports List */}
            {!isLoading && filteredReports.length > 0 && (
              <div className="flex flex-col gap-3 min-w-0">
                {filteredReports.map(({ report, incident }) => {
                  const primarySvc = incident?.primary_service || "unassigned";
                  const generatedUtc = formatUtc(report.generated_at);

                  return (
                    <div
                      key={report.id}
                      className="p-4 sm:p-5 rounded-xl bg-white border border-[#D8D5CD] hover:border-[#1F5A43]/40 flex flex-col lg:flex-row lg:items-center justify-between gap-4 transition-all shadow-xs group"
                    >
                      {/* Left: Report Title & Associated Incident Metadata */}
                      <div className="flex flex-col gap-2 min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge label={report.status} variant={getStatusVariant(report.status)} dot />
                          {incident && (
                            <StatusBadge
                              label={incident.severity}
                              variant={getSeverityVariant(incident.severity)}
                            />
                          )}
                          <span className="font-mono text-[11px] text-[#5F6360] px-1.5 py-0.5 rounded bg-[#F5F3EE] border border-[#D8D5CD]">
                            Report ID: {report.id.slice(0, 8)}...
                          </span>
                          <span className="text-[11px] text-[#5F6360] flex items-center gap-1">
                            <Icon name="schedule" size={14} className="text-[#5F6360]" />
                            <span>Generated {generatedUtc}</span>
                          </span>
                        </div>

                        <div className="flex flex-col gap-1">
                          <button
                            type="button"
                            onClick={() => setSelectedReport({ report, incident })}
                            className="text-left text-[15px] font-semibold text-[#171A18] hover:text-[#1F5A43] transition-colors leading-snug line-clamp-1 cursor-pointer"
                          >
                            {report.title}
                          </button>
                          <div className="flex flex-wrap items-center gap-y-1 gap-x-3 text-[12px] text-[#5F6360]">
                            <span className="flex items-center gap-1 font-mono text-[#171A18] bg-[#F5F3EE] px-2 py-0.5 rounded border border-[#D8D5CD]">
                              <Icon name="lan" size={14} className="text-[#1F5A43]" />
                              <span>Service: {primarySvc}</span>
                            </span>
                            {incident && (
                              <Link
                                href={`/incidents/${incident.id}?tab=reports`}
                                className="text-[#2563EB] hover:underline flex items-center gap-1 font-medium"
                              >
                                <Icon name="open_in_new" size={13} />
                                <span>Incident: {incident.title}</span>
                              </Link>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Right: Actions */}
                      <div className="flex flex-col sm:flex-row lg:flex-col items-start sm:items-center lg:items-end justify-between sm:justify-end gap-3 pt-3 lg:pt-0 border-t lg:border-t-0 border-[#D8D5CD] shrink-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] font-mono text-[#9A7440] px-2 py-0.5 rounded bg-[#FEF7E6] border border-[#FDE8B3]">
                            Causality: Unconfirmed
                          </span>
                        </div>

                        <div className="flex items-center gap-2 w-full sm:w-auto">
                          <button
                            type="button"
                            onClick={() => handleCopyMarkdown(report.markdown_content)}
                            title="Copy full Markdown report to clipboard"
                            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[12px] font-medium text-[#171A18] transition-colors cursor-pointer"
                          >
                            <Icon name="content_copy" size={14} className="text-[#5F6360]" />
                            <span>Copy MD</span>
                          </button>
                          <Link
                            href={`/incidents/${report.incident_id}?tab=reports`}
                            title="Open incident workspace reports tab"
                            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[12px] font-medium text-[#171A18] transition-colors"
                          >
                            <Icon name="open_in_new" size={14} className="text-[#5F6360]" />
                            <span>Workspace</span>
                          </Link>
                          <button
                            type="button"
                            onClick={() => setSelectedReport({ report, incident })}
                            className="flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[13px] font-medium transition-colors shadow-xs cursor-pointer"
                          >
                            <Icon name="visibility" size={15} />
                            <span>Read Retrospective</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Incidents Pending Retrospective */}
        {activeViewTab === "pending" && (
          <div className="flex flex-col gap-3 min-w-0">
            <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-1 text-[13px] text-[#5F6360] shadow-xs">
              <strong className="text-[#171A18] text-[14px]">
                Incidents Eligible for Post-Mortem Generation
              </strong>
              <p>
                Each incident below contains correlated timeline events and verified telemetry evidence. Generating a retrospective synthesizes an executive summary, chronological cascade sequence, and advisory recommendations into PostgreSQL.
              </p>
            </div>

            {pendingIncidents.length === 0 ? (
              <div className="p-8 rounded-xl bg-white border border-[#D8D5CD] flex flex-col items-center justify-center text-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-[#EDF9ED] border border-[#C5ECC4] flex items-center justify-center text-[#2E7D32]">
                  <Icon name="check_circle" size={22} />
                </div>
                <h4 className="text-[15px] font-semibold text-[#171A18]">
                  All Incidents Have Documented Retrospectives
                </h4>
                <p className="text-[12px] text-[#5F6360] max-w-md">
                  Every registered incident in the catalogue currently has at least one persisted post-mortem report.
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {pendingIncidents.map((inc) => (
                  <div
                    key={inc.id}
                    className="p-4 sm:p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-xs"
                  >
                    <div className="flex flex-col gap-1.5 min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge label={inc.severity} variant={getSeverityVariant(inc.severity)} />
                        <StatusBadge label={inc.status} variant={getStatusVariant(inc.status)} dot />
                        <span className="font-mono text-[11px] text-[#5F6360] px-1.5 py-0.5 rounded bg-[#F5F3EE] border border-[#D8D5CD]">
                          {inc.id.slice(0, 8)}...
                        </span>
                        <span className="text-[11px] text-[#5F6360]">
                          Detected {formatUtc(inc.detected_at)}
                        </span>
                      </div>

                      <span className="text-[14px] font-semibold text-[#171A18]">
                        {inc.title}
                      </span>

                      <div className="flex flex-wrap items-center gap-3 text-[12px] text-[#5F6360]">
                        <span className="font-mono text-[#171A18] bg-[#F5F3EE] px-2 py-0.5 rounded border border-[#D8D5CD]">
                          Primary: {inc.primary_service || "unassigned"}
                        </span>
                        <span>
                          Blast Radius: <strong className="text-[#171A18] font-mono">{inc.affected_service_count}</strong> service(s)
                        </span>
                        <span>•</span>
                        <span>
                          Evidence: <strong className="text-[#171A18] font-mono">{inc.evidence_count}</strong> record(s)
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <Link
                        href={`/incidents/${inc.id}?tab=reports`}
                        className="px-3 py-1.5 rounded-lg bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[12px] font-medium text-[#171A18] transition-colors"
                      >
                        Incident Studio
                      </Link>
                      <button
                        type="button"
                        onClick={() => handleGenerateReportForIncident(inc.id)}
                        disabled={generatingIncidentId === inc.id}
                        className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[13px] font-medium transition-colors shadow-xs cursor-pointer disabled:opacity-50"
                      >
                        <Icon
                          name={generatingIncidentId === inc.id ? "refresh" : "summarize"}
                          size={15}
                          className={generatingIncidentId === inc.id ? "animate-spin" : ""}
                        />
                        <span>
                          {generatingIncidentId === inc.id ? "Generating..." : "Generate Retrospective"}
                        </span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* In-App Retrospective Reader Modal / Drawer */}
      {selectedReport && (
        <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-xs flex items-center justify-center p-3 sm:p-6 overflow-y-auto">
          <div className="bg-white border border-[#D8D5CD] rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="p-4 sm:p-5 border-b border-[#D8D5CD] bg-[#F5F3EE] flex items-center justify-between gap-4">
              <div className="flex flex-col gap-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge label={selectedReport.report.status} variant={getStatusVariant(selectedReport.report.status)} dot />
                  <StatusBadge label="CONFIRMED CAUSALITY: FALSE" variant="neutral" />
                  <span className="font-mono text-[11px] text-[#5F6360]">
                    Generated {formatUtc(selectedReport.report.generated_at)}
                  </span>
                </div>
                <h2 className="text-[17px] font-semibold text-[#171A18] truncate">
                  {selectedReport.report.title}
                </h2>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => handleCopyMarkdown(selectedReport.report.markdown_content)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[12px] font-medium transition-colors cursor-pointer"
                >
                  <Icon name={copySuccess ? "check" : "content_copy"} size={15} className={copySuccess ? "text-[#2E7D32]" : "text-[#5F6360]"} />
                  <span>{copySuccess ? "Copied!" : "Copy MD"}</span>
                </button>
                <Link
                  href={`/incidents/${selectedReport.report.incident_id}?tab=reports`}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-[12px] font-medium transition-colors"
                >
                  <Icon name="open_in_new" size={14} />
                  <span>Workspace</span>
                </Link>
                <button
                  type="button"
                  onClick={() => setSelectedReport(null)}
                  className="w-8 h-8 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#5F6360] hover:text-[#171A18] flex items-center justify-center transition-colors cursor-pointer"
                  aria-label="Close reader"
                >
                  <Icon name="close" size={18} />
                </button>
              </div>
            </div>

            {/* Modal Body / Report Sections */}
            <div className="p-4 sm:p-6 overflow-y-auto flex flex-col gap-6 text-[13px] leading-relaxed text-[#171A18]">
              {/* 1. Executive Summary */}
              <div className="flex flex-col gap-2 p-4 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD]">
                <div className="flex items-center gap-2 border-b border-[#D8D5CD] pb-2">
                  <Icon name="info" size={16} className="text-[#1F5A43]" />
                  <h3 className="font-semibold text-[14px] text-[#171A18]">
                    1. Executive Summary &amp; Blast Radius
                  </h3>
                </div>
                <pre className="font-sans whitespace-pre-wrap text-[12px] text-[#5F6360] leading-relaxed">
                  {selectedReport.report.executive_summary}
                </pre>
              </div>

              {/* 2. Chronological Timeline */}
              <div className="flex flex-col gap-2 p-4 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD]">
                <div className="flex items-center gap-2 border-b border-[#D8D5CD] pb-2">
                  <Icon name="timeline" size={16} className="text-[#1F5A43]" />
                  <h3 className="font-semibold text-[14px] text-[#171A18]">
                    2. Chronological Timeline &amp; Cascade
                  </h3>
                </div>
                <pre className="font-mono whitespace-pre-wrap text-[12px] text-[#171A18] leading-relaxed bg-white p-3 rounded-lg border border-[#D8D5CD]">
                  {selectedReport.report.timeline_summary || "No chronological timeline milestones recorded."}
                </pre>
              </div>

              {/* 3. Root Cause Analysis */}
              <div className="flex flex-col gap-2 p-4 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD]">
                <div className="flex items-center gap-2 border-b border-[#D8D5CD] pb-2">
                  <Icon name="psychology" size={16} className="text-[#1F5A43]" />
                  <h3 className="font-semibold text-[14px] text-[#171A18]">
                    3. Root Cause Analysis &amp; Diagnostic Findings
                  </h3>
                </div>
                <div className="whitespace-pre-wrap text-[12px] text-[#171A18] font-mono bg-white p-3 rounded-lg border border-[#D8D5CD] max-h-72 overflow-y-auto leading-relaxed">
                  {selectedReport.report.root_cause_analysis}
                </div>
              </div>

              {/* 4. Advisory Recommendations */}
              <div className="flex flex-col gap-2 p-4 rounded-xl bg-[#F5F3EE] border border-[#D8D5CD]">
                <div className="flex items-center gap-2 border-b border-[#D8D5CD] pb-2">
                  <Icon name="auto_awesome" size={16} className="text-[#9A7440]" />
                  <h3 className="font-semibold text-[14px] text-[#171A18]">
                    4. Advisory Mitigation &amp; Verification Guidance
                  </h3>
                </div>
                <div className="whitespace-pre-wrap text-[12px] text-[#5F6360] leading-relaxed">
                  {selectedReport.report.recommendations_summary || "No advisory recommendations recorded."}
                </div>
              </div>

              {/* 5. Diagnostic Uncertainty & Telemetry Bounds */}
              {selectedReport.report.uncertainty_disclaimer && (
                <div className="p-3.5 rounded-xl bg-[#FEF7E6] border border-[#FDE8B3] text-[#B77900] text-[12px] flex items-start gap-2">
                  <Icon name="warning" size={16} className="shrink-0 mt-0.5" />
                  <span>{selectedReport.report.uncertainty_disclaimer}</span>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-[#D8D5CD] bg-[#F5F3EE] flex items-center justify-between">
              <span className="font-mono text-[11px] text-[#5F6360]">
                PostgreSQL UUID: {selectedReport.report.id}
              </span>
              <button
                type="button"
                onClick={() => setSelectedReport(null)}
                className="px-4 py-1.5 rounded-lg bg-white border border-[#D8D5CD] hover:bg-[#EAE7DF] text-[#171A18] text-[13px] font-medium transition-colors cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
