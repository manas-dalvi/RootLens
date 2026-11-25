"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  fetchIncidents,
  fetchIncidentCandidates,
  persistIncidentCandidate,
  IncidentCandidate,
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
    case "DETECTED":
      return "warning";
    case "INVESTIGATING":
      return "info";
    case "RESOLVED":
      return "healthy";
    default:
      return "neutral";
  }
}

function formatUtcDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  } catch {
    return dateStr;
  }
}

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<IncidentSummaryItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Candidate evaluation state
  const [isEvaluating, setIsEvaluating] = useState<boolean>(false);
  const [candidates, setCandidates] = useState<IncidentCandidate[]>([]);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [persistingId, setPersistingId] = useState<string | null>(null);
  const [persistSuccessMsg, setPersistSuccessMsg] = useState<string | null>(null);

  // Filter and refresh trigger state
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const loadIncidents = useCallback(() => {
    setRefreshTrigger((prev) => prev + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function execute() {
      await Promise.resolve();
      if (cancelled) return;

      setIsLoading(true);
      setError(null);

      try {
        const data = await fetchIncidents({
          severity: severityFilter || undefined,
          status: statusFilter || undefined,
          page_size: 50,
        });
        if (cancelled) return;
        setIncidents(data.items || []);
        setTotalCount(data.total || 0);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Failed to load incidents from backend.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    execute();

    return () => {
      cancelled = true;
    };
  }, [severityFilter, statusFilter, refreshTrigger]);

  const handleEvaluateCandidates = async () => {
    setIsEvaluating(true);
    setCandidateError(null);
    setPersistSuccessMsg(null);
    try {
      const data = await fetchIncidentCandidates({
        window_minutes: 15,
        cascade_window_seconds: 120,
        max_logs: 1000,
      });
      setCandidates(data.candidates || []);
      if (!data.candidates || data.candidates.length === 0) {
        setCandidateError("Zero candidate incidents detected in recent log window. Verify error logs are indexed in OpenSearch.");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setCandidateError(err.message);
      } else {
        setCandidateError("Failed to query log telemetry for candidate evaluation.");
      }
    } finally {
      setIsEvaluating(false);
    }
  };

  const handlePersistCandidate = async (candidate: IncidentCandidate) => {
    setPersistingId(candidate.candidate_id);
    setPersistSuccessMsg(null);
    try {
      const res = await persistIncidentCandidate(candidate);
      setPersistSuccessMsg(`Incident '${res.title}' successfully persisted with ID ${res.id}.`);
      // Refresh persisted incidents list
      await loadIncidents();
      // Remove candidate from local list
      setCandidates((prev) => prev.filter((c) => c.candidate_id !== candidate.candidate_id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to persist incident");
    } finally {
      setPersistingId(null);
    }
  };

  return (
    <div className="flex flex-col w-full p-4 lg:p-6 gap-6">
      {/* Header bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-2 border-b border-[#EDEFF3]">
        <div className="flex flex-col gap-1">
          <h1 className="text-[22px] font-semibold text-[#1B1A1A] tracking-tight">
            Incidents & Triage
          </h1>
          <p className="text-[13px] text-[#606366]">
            Incident catalogue, cross-service correlation timeline, and evidence-grounded triage.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={handleEvaluateCandidates}
            disabled={isEvaluating}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[13px] font-medium transition-colors disabled:opacity-50"
          >
            <Icon name={isEvaluating ? "sync" : "radar"} size={16} className={`text-[#606366] ${isEvaluating ? "animate-spin" : ""}`} />
            <span>{isEvaluating ? "Evaluating Logs..." : "Evaluate Log Candidates"}</span>
          </button>

          <Link
            href="/logs"
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#606366] text-[13px] font-medium transition-colors"
          >
            <Icon name="search" size={16} className="text-[#9B9FA3]" />
            <span>Log Explorer</span>
          </Link>
        </div>
      </div>

      {/* Success Banner */}
      {persistSuccessMsg && (
        <div className="p-4 rounded-xl bg-[#EDF9ED] border border-[#C5ECC4] flex items-center justify-between gap-3 text-[#2E7D32]">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Icon name="check_circle" size={18} />
            <span>{persistSuccessMsg}</span>
          </div>
          <button
            type="button"
            onClick={() => setPersistSuccessMsg(null)}
            className="text-[12px] font-semibold hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Candidate Evaluation Drawer / Panel (if candidates loaded or error) */}
      {(candidates.length > 0 || candidateError) && (
        <div className="p-5 rounded-xl bg-white border border-[#BFDBFE] flex flex-col gap-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Icon name="radar" size={18} className="text-[#4D80FC]" />
              <h3 className="text-[14px] font-semibold text-[#1B1A1A]">
                Evaluated Incident Candidates (In-Memory Telemetry)
              </h3>
              <StatusBadge label={`${candidates.length} CANDIDATE(S)`} variant="info" />
            </div>
            <button
              type="button"
              onClick={() => {
                setCandidates([]);
                setCandidateError(null);
              }}
              className="text-[12px] text-[#9B9FA3] hover:text-[#1B1A1A]"
            >
              Close
            </button>
          </div>

          <p className="text-[12px] text-[#606366]">
            These candidates were evaluated dynamically from current OpenSearch error spikes and cross-service traces. Candidate evaluation is read-only; click &quot;Persist to Catalog&quot; to permanently store an incident record into PostgreSQL.
          </p>

          {candidateError && (
            <div className="p-3 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] text-[#B77900] text-[12px]">
              {candidateError}
            </div>
          )}

          {candidates.map((cand) => {
            const hasSequenceAnomaly = Boolean(
              cand.correlation_rules_used?.includes("SEQUENCE_ANOMALY") ||
              cand.cluster_ids?.some((id) => id.startsWith("cl-logbert-")) ||
              cand.timeline_events?.some((e) => e.event_type === "SEQUENCE_ANOMALY") ||
              cand.evidence_items?.some((e) => e.evidence_type === "SEQUENCE_ANOMALY")
            );

            return (
              <div
                key={cand.candidate_id}
                className="p-4 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] flex flex-col sm:flex-row sm:items-center justify-between gap-3"
              >
                <div className="flex flex-col gap-1.5 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-[#606366]">
                      {cand.candidate_id}
                    </span>
                    <StatusBadge label={cand.severity} variant={getSeverityVariant(cand.severity)} />
                    {hasSequenceAnomaly && (
                      <StatusBadge label="Sequence Anomaly" variant="info" mono={false} />
                    )}
                    {cand.correlation_rules_used
                      ?.filter((rule) => rule !== "SEQUENCE_ANOMALY")
                      .map((rule) => (
                        <StatusBadge key={rule} label={rule} variant="neutral" />
                      ))}
                  </div>
                  <h4 className="text-[14px] font-medium text-[#1B1A1A] truncate">
                    {cand.title}
                  </h4>
                  <div className="flex flex-wrap items-center gap-x-4 text-[12px] text-[#606366]">
                    <span>Timeline: {formatUtcDate(cand.started_at)}</span>
                    <span>Affected: {cand.affected_services.length} services</span>
                    <span>Events: {cand.timeline_events.length}</span>
                    <span>Evidence: {cand.evidence_items.length} records</span>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => handlePersistCandidate(cand)}
                  disabled={persistingId === cand.candidate_id}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#4D80FC] hover:bg-[#3B6BE0] text-white text-[13px] font-medium transition-colors shrink-0 disabled:opacity-50"
                >
                  <Icon name={persistingId === cand.candidate_id ? "sync" : "save"} size={16} className={persistingId === cand.candidate_id ? "animate-spin" : ""} />
                  <span>{persistingId === cand.candidate_id ? "Persisting..." : "Persist to Catalog"}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Filter and Control Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3 rounded-xl bg-white border border-[#EDEFF3]">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5 text-[13px] text-[#606366]">
            <Icon name="filter_list" size={16} className="text-[#9B9FA3]" />
            <span className="font-medium">Filter:</span>
          </div>

          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="px-2.5 py-1 rounded-lg border border-[#EDEFF3] bg-white text-[12px] text-[#1B1A1A] focus:outline-hidden focus:border-[#4D80FC]"
          >
            <option value="">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2.5 py-1 rounded-lg border border-[#EDEFF3] bg-white text-[12px] text-[#1B1A1A] focus:outline-hidden focus:border-[#4D80FC]"
          >
            <option value="">All Statuses</option>
            <option value="DETECTED">Detected</option>
            <option value="INVESTIGATING">Investigating</option>
            <option value="RESOLVED">Resolved</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[12px] text-[#9B9FA3]">
            Total Records: <strong className="font-semibold text-[#1B1A1A]">{totalCount}</strong>
          </span>
          <button
            type="button"
            onClick={loadIncidents}
            disabled={isLoading}
            className="p-1 rounded-lg border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#606366] transition-colors"
            title="Refresh catalogue"
          >
            <Icon name="refresh" size={16} className={isLoading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-between gap-3 text-[#E63316]">
          <div className="flex items-center gap-2 text-[13px]">
            <Icon name="error" size={18} />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={loadIncidents}
            className="px-3 py-1 rounded-lg bg-white border border-[#FCD3CE] text-[12px] font-medium hover:bg-[#FDF2F1]"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading state skeleton */}
      {isLoading && (
        <div className="p-6 rounded-xl bg-white border border-[#EDEFF3] flex flex-col gap-3">
          <div className="h-5 w-48 bg-[#F5F6F9] rounded-sm animate-pulse" />
          <div className="h-12 w-full bg-[#F5F6F9] rounded-lg animate-pulse" />
          <div className="h-12 w-full bg-[#F5F6F9] rounded-lg animate-pulse" />
          <div className="h-12 w-full bg-[#F5F6F9] rounded-lg animate-pulse" />
        </div>
      )}

      {/* Empty State when no persisted incidents exist */}
      {!isLoading && !error && incidents.length === 0 && (
        <div className="p-8 rounded-xl bg-white border border-[#EDEFF3] flex flex-col items-center justify-center text-center gap-4 max-w-2xl mx-auto shadow-xs my-4">
          <div className="w-12 h-12 rounded-xl bg-[#F5F6F9] border border-[#EDEFF3] flex items-center justify-center text-[#606366]">
            <Icon name="inbox" size={26} />
          </div>
          <div className="flex flex-col gap-1.5">
            <h2 className="text-[17px] font-semibold text-[#1B1A1A]">
              No Persisted Incidents Found
            </h2>
            <p className="text-[13px] text-[#606366] leading-relaxed">
              No incidents currently match the filter criteria in the PostgreSQL catalog. You can evaluate candidate incidents from indexed log telemetry or explore logs directly.
            </p>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <button
              type="button"
              onClick={handleEvaluateCandidates}
              disabled={isEvaluating}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#4D80FC] text-white hover:bg-[#3B6BE0] text-[13px] font-medium transition-colors"
            >
              <Icon name="radar" size={16} />
              <span>Evaluate Candidates from Logs</span>
            </button>

            <Link
              href="/logs"
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[13px] font-medium transition-colors"
            >
              <Icon name="search" size={16} className="text-[#606366]" />
              <span>Search Log Explorer</span>
            </Link>
          </div>
        </div>
      )}

      {/* Persisted Incident Catalogue Table */}
      {!isLoading && !error && incidents.length > 0 && (
        <div className="rounded-xl bg-white border border-[#EDEFF3] overflow-hidden shadow-xs">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-[#EDEFF3] bg-[#F9FAFB] text-[11px] font-semibold text-[#606366] uppercase tracking-wider">
                  <th className="py-3 px-4">Incident Title / ID</th>
                  <th className="py-3 px-4">Severity</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Started (UTC)</th>
                  <th className="py-3 px-4">Primary Service</th>
                  <th className="py-3 px-4">Blast Radius</th>
                  <th className="py-3 px-4">Evidence</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#EDEFF3] text-[13px]">
                {incidents.map((inc) => (
                  <tr key={inc.id} className="hover:bg-[#F9FAFC] transition-colors">
                    <td className="py-3.5 px-4">
                      <div className="flex flex-col gap-0.5">
                        <Link
                          href={`/incidents/${inc.id}`}
                          className="font-medium text-[#1B1A1A] hover:text-[#1F5A43] transition-colors"
                        >
                          {inc.title}
                        </Link>
                        <span className="font-mono text-[11px] text-[#9B9FA3]">
                          {inc.id}
                        </span>
                      </div>
                    </td>
                    <td className="py-3.5 px-4">
                      <StatusBadge label={inc.severity} variant={getSeverityVariant(inc.severity)} />
                    </td>
                    <td className="py-3.5 px-4">
                      <StatusBadge label={inc.status} variant={getStatusVariant(inc.status)} dot />
                    </td>
                    <td className="py-3.5 px-4 font-mono text-[12px] text-[#606366]">
                      {formatUtcDate(inc.started_at)}
                    </td>
                    <td className="py-3.5 px-4">
                      <span className="font-mono text-[12px] text-[#1B1A1A]">
                        {inc.primary_service || "Unassigned"}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 text-[#606366] text-[12px]">
                      {inc.affected_service_count} service(s)
                    </td>
                    <td className="py-3.5 px-4">
                      <div className="flex items-center gap-1.5 text-[12px] text-[#606366]">
                        <Icon name="description" size={14} className="text-[#9B9FA3]" />
                        <span>{inc.evidence_count} items</span>
                      </div>
                    </td>
                    <td className="py-3.5 px-4 text-right">
                      <Link
                        href={`/incidents/${inc.id}`}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-[#F5F3EE] hover:bg-[#EAE7DF] border border-[#D8D5CD] text-[#1F5A43] text-[12px] font-medium transition-colors"
                      >
                        <span>Investigate</span>
                        <Icon name="arrow_forward" size={14} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
