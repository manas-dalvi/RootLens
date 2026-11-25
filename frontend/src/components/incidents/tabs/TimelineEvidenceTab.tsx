"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  CorrelatedTimelineEvent,
  CorrelatedEvidenceItem,
  IncidentDetailResponse,
} from "@/lib/api/incidents";

export interface TimelineEvidenceTabProps {
  timelineEvents: CorrelatedTimelineEvent[];
  evidenceItems: CorrelatedEvidenceItem[];
  incident?: IncidentDetailResponse;
  className?: string;
}

function formatUtc(dateStr?: string | null): string {
  if (!dateStr) return "N/A";
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  } catch {
    return dateStr;
  }
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

export function TimelineEvidenceTab({
  timelineEvents,
  evidenceItems,
  className = "",
}: TimelineEvidenceTabProps) {
  // Local state for expandable event attributes
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());

  // Local state for evidence snippet modal
  const [selectedEvidence, setSelectedEvidence] = useState<CorrelatedEvidenceItem | null>(null);
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // Toggle expansion of event attributes
  const toggleEventExpansion = (sequenceOrder: number) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(sequenceOrder)) {
        next.delete(sequenceOrder);
      } else {
        next.add(sequenceOrder);
      }
      return next;
    });
  };

  // Open evidence detail modal
  const openEvidenceModal = (item: CorrelatedEvidenceItem, triggerEl: HTMLElement | null) => {
    triggerRef.current = triggerEl;
    setSelectedEvidence(item);
    setIsCopied(false);
  };

  // Close evidence detail modal
  const closeEvidenceModal = () => {
    setSelectedEvidence(null);
    setIsCopied(false);
  };

  // Copy snippet to clipboard
  const handleCopySnippet = async (snippet: string) => {
    try {
      await navigator.clipboard.writeText(snippet);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  // Modal accessibility: Escape key and focus trapping
  useEffect(() => {
    if (!selectedEvidence) return;

    const triggerEl = triggerRef.current;

    const timer = setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeEvidenceModal();
        return;
      }

      if (e.key === "Tab" && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) {
          e.preventDefault();
          modalRef.current.focus();
          return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first || !modalRef.current.contains(document.activeElement)) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last || !modalRef.current.contains(document.activeElement)) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      clearTimeout(timer);
      window.removeEventListener("keydown", handleKeyDown);
      triggerEl?.focus();
    };
  }, [selectedEvidence]);

  // Ensure chronological ordering
  const sortedEvents = [...timelineEvents].sort(
    (a, b) => a.sequence_order - b.sequence_order
  );

  return (
    <div className={`flex flex-col gap-5 w-full ${className}`}>
      {/* 1. Timeline Milestones Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        <div className="flex items-center justify-between border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="timeline" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              Correlated Event Timeline ({sortedEvents.length} Milestones)
            </h3>
          </div>
          <StatusBadge label="CHRONOLOGICAL ASC" variant="neutral" />
        </div>

        {sortedEvents.length === 0 ? (
          <div className="p-6 text-center text-[13px] text-[#5F6360]">
            No timeline events recorded for this incident.
          </div>
        ) : (
          <div className="relative pl-6 border-l-2 border-[#D8D5CD] ml-3 flex flex-col gap-6 py-2">
            {sortedEvents.map((ev) => {
              const isExpanded = expandedEvents.has(ev.sequence_order);
              const hasAttributes =
                ev.attributes && Object.keys(ev.attributes).length > 0;

              return (
                <div key={ev.sequence_order} className="relative flex flex-col gap-1.5">
                  {/* Node Bullet */}
                  <span
                    className={`absolute -left-[31px] top-0.5 w-3.5 h-3.5 rounded-full border-2 bg-white ${ev.is_earliest_observed_failure
                        ? "border-[#A34B4B] ring-4 ring-[#FEECEB]"
                        : "border-[#1F5A43]"
                      }`}
                  />

                  {/* Header Meta */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold text-[#5F6360]">
                      #{ev.sequence_order}
                    </span>
                    <StatusBadge
                      label={ev.event_type}
                      variant={ev.is_earliest_observed_failure ? "critical" : "info"}
                      dot={ev.is_earliest_observed_failure}
                    />
                    {ev.is_earliest_observed_failure && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#FEECEB] text-[#A34B4B] border border-[#FCD3CE] font-semibold">
                        EARLIEST FAILURE
                      </span>
                    )}
                    <span className="font-mono text-[12px] font-semibold text-[#171A18]">
                      {ev.service_name}
                    </span>
                    <span className="font-mono text-[11px] text-[#5F6360]">
                      {formatUtc(ev.event_timestamp)}
                    </span>
                    <span className="font-mono text-[11px] text-[#171A18] bg-[#F5F3EE] border border-[#D8D5CD] px-1.5 py-0.5 rounded">
                      +{ev.propagation_lag_seconds.toFixed(1)}s lag
                    </span>
                    {(() => {
                      const relType = ev.relationship_type || (ev.attributes?.relationship_type as string);
                      if (!relType) return null;
                      if (relType === "TRACE_BASED") {
                        return (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded font-semibold bg-[#EDF9ED] text-[#1F5A43] border border-[#C5ECC4]">
                            TRACE-LINKED (100%)
                          </span>
                        );
                      }
                      if (relType === "SPAN_BASED") {
                        return (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded font-semibold bg-[#E8F4FD] text-[#0C5460] border border-[#BEE5EB]">
                            SPAN-LINKED (80%)
                          </span>
                        );
                      }
                      if (relType === "REQUEST_BASED") {
                        return (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded font-semibold bg-[#EFF6FF] text-[#1D4ED8] border border-[#BFDBFE]">
                            REQUEST-LINKED (70%)
                          </span>
                        );
                      }
                      if (relType === "SERVICE_LOCAL") {
                        return (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded font-semibold bg-[#F5F3EE] text-[#5F6360] border border-[#D8D5CD]">
                            SERVICE-LOCAL
                          </span>
                        );
                      }
                      if (relType === "TEMPORAL_ASSOCIATION" || relType === "SUSPECTED_DEPENDENCY") {
                        return (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded font-semibold bg-[#FEF3C7] text-[#92400E] border border-[#FCD34D]" title="Non-causal heuristic temporal association">
                            TEMPORAL CASCADE (35%)
                          </span>
                        );
                      }
                      return null;
                    })()}
                    {ev.attributes?.model === "LogBERT" && (
                      <span
                        className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold border ${ev.attributes.is_mlkp_mismatch
                            ? "bg-[#FEF3C7] text-[#92400E] border-[#FCD34D]"
                            : "bg-[#F3E8FF] text-[#6B21A8] border-[#D8B4FE]"
                          }`}
                      >
                        {ev.attributes.is_mlkp_mismatch ? "MLKP MISMATCH" : "VHM EMBEDDING"}
                      </span>
                    )}
                  </div>

                  {/* Summary */}
                  <p className="text-[13px] text-[#171A18] leading-relaxed">
                    {ev.summary}
                  </p>

                  {/* Badges / Links */}
                  <div className="flex flex-wrap items-center gap-2 pt-0.5">
                    {ev.trace_id ? (
                      <span className="font-mono text-[11px] text-[#5F6360] bg-[#F5F3EE] border border-[#D8D5CD] px-2 py-0.5 rounded">
                        trace: {ev.trace_id}
                      </span>
                    ) : (
                      <span className="font-mono text-[10px] text-[#8C908D] border border-dashed border-[#D8D5CD] px-1.5 py-0.5 rounded" title="Distributed trace propagation headers absent for this event">
                        no trace context
                      </span>
                    )}
                    {ev.span_id && (
                      <span className="font-mono text-[11px] text-[#5F6360] bg-[#F5F3EE] border border-[#D8D5CD] px-2 py-0.5 rounded">
                        span: {ev.span_id}
                      </span>
                    )}
                    {ev.sample_log_id && (
                      <Link
                        href={`/logs?q=${encodeURIComponent(ev.sample_log_id)}`}
                        className="font-mono text-[11px] text-[#1F5A43] hover:underline flex items-center gap-1"
                      >
                        <span>view log: {ev.sample_log_id}</span>
                        <Icon name="open_in_new" size={12} />
                      </Link>
                    )}
                    {hasAttributes && (
                      <button
                        type="button"
                        onClick={() => toggleEventExpansion(ev.sequence_order)}
                        className="text-[11px] font-medium text-[#5F6360] hover:text-[#171A18] flex items-center gap-1 ml-auto cursor-pointer"
                        aria-expanded={isExpanded}
                      >
                        <span>{isExpanded ? "Hide Details" : "Show Details"}</span>
                        <Icon
                          name={isExpanded ? "expand_less" : "expand_more"}
                          size={14}
                        />
                      </button>
                    )}
                  </div>

                  {/* Expandable Attributes & Explainability */}
                  {isExpanded && hasAttributes && (
                    <div className="mt-2 flex flex-col gap-2.5">
                      {/* LogBERT Token-Level Explanation Card */}
                      {ev.attributes?.model === "LogBERT" && (() => {
                        const isMismatch = Boolean(ev.attributes.is_mlkp_mismatch);
                        const expType = (ev.attributes.explanation_type as string) || (isMismatch ? "TOKEN_MISMATCH" : "EMBEDDING_DISTANCE");
                        const dominant = (ev.attributes.dominant_factor as string) || (isMismatch ? "MLKP_MISMATCH" : "VHM_DISTANCE");
                        const score = typeof ev.attributes.anomaly_score === "number" ? ev.attributes.anomaly_score : null;
                        const thresh = typeof ev.attributes.threshold === "number" ? ev.attributes.threshold : null;
                        const vhm = typeof ev.attributes.vhm_distance === "number" ? ev.attributes.vhm_distance : null;
                        const mismatchRatio = typeof ev.attributes.mismatch_ratio === "number" ? ev.attributes.mismatch_ratio : null;
                        const observed = (ev.attributes.mlkp_observed_event as string) || null;
                        const observedProb = typeof ev.attributes.mlkp_observed_prob === "number" ? ev.attributes.mlkp_observed_prob : null;
                        const observedNll = typeof ev.attributes.mlkp_observed_nll === "number" ? ev.attributes.mlkp_observed_nll : null;
                        const topCands = (ev.attributes.mlkp_top_candidates || []) as Array<{
                          event: string;
                          token_id: number;
                          probability: number;
                        }>;

                        return (
                          <div className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col gap-3 text-[12px]">
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#D8D5CD] pb-2">
                              <div className="flex items-center gap-2">
                                <Icon name="psychology" size={16} className="text-[#1F5A43]" />
                                <span className="font-semibold text-[#171A18] text-[13px]">
                                  LogBERT Token-Level Anomaly Diagnosis
                                </span>
                              </div>
                              <div className="flex items-center gap-1.5 font-mono text-[11px]">
                                <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD] text-[#171A18]">
                                  Score: {score !== null ? score.toFixed(4) : "N/A"}
                                </span>
                                <span className="text-[#5F6360]">/</span>
                                <span className="px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD] text-[#5F6360]">
                                  Threshold: {thresh !== null ? thresh.toFixed(4) : "N/A"}
                                </span>
                              </div>
                            </div>

                            {/* Metric Badges */}
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
                              <div className="p-2 rounded bg-white border border-[#D8D5CD] flex flex-col">
                                <span className="text-[#5F6360] text-[10px] uppercase font-semibold">Diagnosis</span>
                                <span className="font-bold text-[#171A18] truncate">{expType}</span>
                              </div>
                              <div className="p-2 rounded bg-white border border-[#D8D5CD] flex flex-col">
                                <span className="text-[#5F6360] text-[10px] uppercase font-semibold">Dominant Signal</span>
                                <span className="font-mono text-[#171A18] truncate">{dominant}</span>
                              </div>
                              <div className="p-2 rounded bg-white border border-[#D8D5CD] flex flex-col">
                                <span className="text-[#5F6360] text-[10px] uppercase font-semibold">MLKP Mismatch</span>
                                <span className="font-mono text-[#171A18]">
                                  {mismatchRatio !== null ? `${(mismatchRatio * 100).toFixed(1)}%` : (isMismatch ? "True" : "False")}
                                </span>
                              </div>
                              <div className="p-2 rounded bg-white border border-[#D8D5CD] flex flex-col">
                                <span className="text-[#5F6360] text-[10px] uppercase font-semibold">VHM Drift (||h-c||²)</span>
                                <span className="font-mono text-[#171A18]">
                                  {vhm !== null ? vhm.toFixed(4) : "N/A"}
                                </span>
                              </div>
                            </div>

                            {/* Observed Event vs Candidates */}
                            {observed && (
                              <div className="p-3 rounded-lg bg-white border border-[#D8D5CD] flex flex-col gap-2">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-[11px] font-semibold text-[#5F6360] uppercase">
                                      Observed Event:
                                    </span>
                                    <span className={`px-2 py-0.5 rounded font-mono text-[11px] font-bold border ${isMismatch
                                        ? "bg-[#FEECEB] text-[#A34B4B] border-[#FCD3CE]"
                                        : "bg-[#EDF9ED] text-[#1F5A43] border-[#C5ECC4]"
                                      }`}>
                                      {observed}
                                    </span>
                                    {observedProb !== null && (
                                      <span className="text-[11px] font-mono text-[#5F6360]">
                                        (p = {(observedProb * 100).toFixed(2)}%{observedNll !== null ? `, NLL = ${observedNll.toFixed(2)}` : ""})
                                      </span>
                                    )}
                                  </div>
                                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold ${isMismatch
                                      ? "bg-[#FEECEB] text-[#A34B4B]"
                                      : "bg-[#EDF9ED] text-[#1F5A43]"
                                    }`}>
                                    {isMismatch ? "UNEXPECTED EXECUTION TRANSITION" : "PREDICTED EXECUTION TRANSITION"}
                                  </span>
                                </div>

                                {topCands.length > 0 && (
                                  <div className="flex flex-col gap-1.5 pt-1 border-t border-[#EDEFF3]">
                                    <span className="text-[10.5px] font-semibold text-[#5F6360] uppercase">
                                      Expected Candidate Templates (Top-{topCands.length} Predictions):
                                    </span>
                                    <div className="flex flex-wrap gap-1.5">
                                      {topCands.map((c, cIdx) => (
                                        <div
                                          key={cIdx}
                                          className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#F5F3EE] border border-[#D8D5CD] font-mono text-[11px]"
                                        >
                                          <span className="font-semibold text-[#171A18]">{c.event}</span>
                                          <span className="text-[10px] text-[#5F6360]">
                                            {(c.probability * 100).toFixed(1)}%
                                          </span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      <div className="p-3 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[12px] flex flex-col gap-1.5">
                        <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                          Telemetry Event Attributes
                        </span>
                        <pre className="font-mono text-[11px] text-[#171A18] overflow-x-auto p-2 bg-white rounded border border-[#D8D5CD] leading-relaxed">
                          {JSON.stringify(ev.attributes, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 2. Linked Evidence Records Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        <div className="flex items-center justify-between border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="description" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              Linked Evidence Records ({evidenceItems.length})
            </h3>
          </div>
          <StatusBadge label="LOG RECORDS LINKED" variant="neutral" />
        </div>

        {evidenceItems.length === 0 ? (
          <div className="p-6 text-center text-[13px] text-[#5F6360]">
            No evidence items linked.
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {evidenceItems.map((item, idx) => (
              <div
                key={item.log_id || idx}
                className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col gap-2.5 transition-colors"
              >
                {/* Header Meta */}
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <StatusBadge label={item.evidence_type} variant="info" />
                    <span className="font-mono text-[12px] font-semibold text-[#171A18]">
                      {item.service_name}
                    </span>
                    <StatusBadge
                      label={item.log_level}
                      variant={getSeverityVariant(item.log_level)}
                    />
                    <span className="font-mono text-[11px] text-[#5F6360]">
                      rule: {item.correlation_rule}
                    </span>
                    {item.relationship_type && (
                      <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-white border border-[#D8D5CD] text-[#171A18] font-semibold">
                        {item.relationship_type}
                      </span>
                    )}
                    {typeof item.correlation_confidence === "number" && (
                      <span className="font-mono text-[10px] text-[#5F6360]">
                        ({Math.round(item.correlation_confidence * 100)}% conf)
                      </span>
                    )}
                  </div>
                  <span className="font-mono text-[11px] text-[#5F6360]">
                    {formatUtc(item.timestamp)}
                  </span>
                </div>

                {/* Log Snippet Box */}
                <div className="relative group">
                  <div className="p-3 rounded-lg bg-[#171A18] text-[#F5F3EE] font-mono text-[11.5px] overflow-x-auto whitespace-pre-wrap leading-relaxed">
                    {item.snippet}
                  </div>
                  <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={(e) => openEvidenceModal(item, e.currentTarget)}
                      className="px-2 py-1 rounded bg-[#2D312E] hover:bg-[#3D423E] text-white text-[10px] font-mono flex items-center gap-1 cursor-pointer"
                      title="Inspect full snippet"
                    >
                      <Icon name="fullscreen" size={12} />
                      <span>Expand</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleCopySnippet(item.snippet)}
                      className="px-2 py-1 rounded bg-[#2D312E] hover:bg-[#3D423E] text-white text-[10px] font-mono flex items-center gap-1 cursor-pointer"
                      title="Copy snippet"
                    >
                      <Icon name="content_copy" size={12} />
                      <span>Copy</span>
                    </button>
                  </div>
                </div>

                {/* Footer Rationale & Links */}
                <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-[#5F6360] pt-1">
                  <span className="leading-relaxed">{item.rationale}</span>
                  <div className="flex items-center gap-3 shrink-0">
                    {item.content_hash && (
                      <span
                        className="font-mono text-[10px] text-[#5F6360]"
                        title={item.content_hash}
                      >
                        hash: {item.content_hash.slice(0, 10)}...
                      </span>
                    )}
                    <Link
                      href={`/logs?q=${encodeURIComponent(item.log_id)}`}
                      className="text-[#1F5A43] hover:underline font-mono text-[11px] flex items-center gap-1"
                    >
                      <span>inspect OpenSearch record</span>
                      <Icon name="arrow_forward" size={12} />
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 3. Evidence Snippet Detail Modal */}
      {selectedEvidence && (
        <div
          role="presentation"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#171A18]/45"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeEvidenceModal();
            }
          }}
        >
          <div
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="evidence-modal-title"
            aria-describedby="evidence-modal-description"
            tabIndex={-1}
            className="w-full max-w-2xl bg-white border border-[#D8D5CD] rounded-xl shadow-xl p-6 flex flex-col gap-4 text-[#171A18] focus:outline-none max-h-[85vh] overflow-y-auto"
          >
            {/* Modal Header */}
            <div className="flex items-start justify-between gap-3 border-b border-[#D8D5CD] pb-3">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-[#EDF9ED] text-[#1F5A43] border border-[#C5ECC4] flex items-center justify-center">
                  <Icon name="description" size={18} />
                </div>
                <div className="flex flex-col">
                  <h2 id="evidence-modal-title" className="text-[16px] font-semibold text-[#171A18]">
                    Evidence Log Snippet
                  </h2>
                  <span className="font-mono text-[11px] text-[#5F6360]">
                    Log ID: {selectedEvidence.log_id}
                  </span>
                </div>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={closeEvidenceModal}
                className="text-[#5F6360] hover:text-[#171A18] p-1 rounded-md transition-colors cursor-pointer"
                aria-label="Close evidence modal"
              >
                <Icon name="close" size={18} />
              </button>
            </div>

            {/* Modal Metadata Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[12px]">
              <div className="p-2 rounded bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col">
                <span className="text-[#5F6360] text-[10px] uppercase">Service</span>
                <span className="font-mono font-semibold">{selectedEvidence.service_name}</span>
              </div>
              <div className="p-2 rounded bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col">
                <span className="text-[#5F6360] text-[10px] uppercase">Type</span>
                <span className="font-semibold">{selectedEvidence.evidence_type}</span>
              </div>
              <div className="p-2 rounded bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col">
                <span className="text-[#5F6360] text-[10px] uppercase">Log Level</span>
                <span className="font-mono font-semibold">{selectedEvidence.log_level}</span>
              </div>
              <div className="p-2 rounded bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col">
                <span className="text-[#5F6360] text-[10px] uppercase">Timestamp</span>
                <span className="font-mono text-[11px]">{formatUtc(selectedEvidence.timestamp)}</span>
              </div>
            </div>

            {/* Rationale */}
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                Correlation Rationale
              </span>
              <p id="evidence-modal-description" className="text-[13px] text-[#171A18] leading-relaxed">
                {selectedEvidence.rationale} (Rule: <span className="font-mono">{selectedEvidence.correlation_rule}</span>)
              </p>
            </div>

            {/* Snippet Display */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Raw Log Snippet
                </span>
                <button
                  type="button"
                  onClick={() => handleCopySnippet(selectedEvidence.snippet)}
                  className="text-[11px] text-[#1F5A43] hover:underline flex items-center gap-1 cursor-pointer font-medium"
                >
                  <Icon name={isCopied ? "check" : "content_copy"} size={13} />
                  <span>{isCopied ? "Copied!" : "Copy Snippet"}</span>
                </button>
              </div>
              <div className="p-3.5 rounded-lg bg-[#171A18] text-[#F5F3EE] font-mono text-[12px] overflow-x-auto whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto">
                {selectedEvidence.snippet}
              </div>
            </div>

            {/* Content Hash & OpenSearch Action */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-[#D8D5CD]">
              {selectedEvidence.content_hash && (
                <div className="flex items-center gap-1.5 font-mono text-[11px] text-[#5F6360]">
                  <span>SHA-256:</span>
                  <span className="select-all" title={selectedEvidence.content_hash}>
                    {selectedEvidence.content_hash}
                  </span>
                </div>
              )}
              <div className="flex items-center gap-2 ml-auto">
                <Link
                  href={`/logs?q=${encodeURIComponent(selectedEvidence.log_id)}`}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#1F5A43] hover:bg-[#184634] text-white text-[12px] font-medium transition-colors shadow-xs"
                >
                  <span>Inspect in Log Explorer</span>
                  <Icon name="open_in_new" size={14} />
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

