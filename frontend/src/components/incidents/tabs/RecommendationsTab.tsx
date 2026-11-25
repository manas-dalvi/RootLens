"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import type {
  IncidentDetailResponse,
  RecommendationListResponse,
  RecommendationResponse,
} from "@/lib/api/incidents";

export interface RecommendationsTabProps {
  incident: IncidentDetailResponse;
  recommendations: RecommendationListResponse | null;
  isRecommendationsLoading: boolean;
  isGeneratingRecommendations: boolean;
  recommendationsError: string | null;
  onFetchRecommendations: () => Promise<void> | void;
  onGenerateRecommendations: (forceRegenerate?: boolean) => Promise<void> | void;
  className?: string;
}

function formatUtc(timestamp: string): string {
  try {
    const d = new Date(timestamp);
    if (isNaN(d.getTime())) return timestamp;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  } catch {
    return timestamp;
  }
}

function getClassificationVariant(classification: string): StatusVariant {
  switch (classification?.toUpperCase()) {
    case "TRACE_LINKED":
      return "info";
    case "TEMPORAL_ONLY":
      return "warning";
    case "INSUFFICIENT":
      return "critical";
    default:
      return "neutral";
  }
}

function getPriorityVariant(priority: string): StatusVariant {
  switch (priority?.toUpperCase()) {
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

function getActionTypeVariant(actionType: string): StatusVariant {
  switch (actionType?.toUpperCase()) {
    case "IMMEDIATE_ACTION":
      return "critical";
    case "MITIGATION_ADVISORY":
      return "warning";
    case "DIAGNOSTIC_VERIFICATION":
      return "info";
    case "TELEMETRY_GAP":
      return "neutral";
    default:
      return "neutral";
  }
}

export function RecommendationsTab({
  incident,
  recommendations,
  isRecommendationsLoading,
  isGeneratingRecommendations,
  recommendationsError,
  onFetchRecommendations,
  onGenerateRecommendations,
  className = "",
}: RecommendationsTabProps) {
  // Group recommendations by priority while preserving original sequence ordering
  const items = recommendations?.items;
  const groupedRecommendations: {
    priority: string;
    items: RecommendationResponse[];
  }[] = [];

  if (items && items.length > 0) {
    const priorityBuckets: Record<string, RecommendationResponse[]> = {
      CRITICAL: [],
      HIGH: [],
      MEDIUM: [],
      LOW: [],
    };
    const uncategorized: RecommendationResponse[] = [];

    for (const item of items) {
      const p = (item.priority || "").toUpperCase();
      if (priorityBuckets[p]) {
        priorityBuckets[p].push(item);
      } else {
        uncategorized.push(item);
      }
    }

    for (const priority of ["CRITICAL", "HIGH", "MEDIUM", "LOW"]) {
      if (priorityBuckets[priority].length > 0) {
        groupsPush: {
          groupedRecommendations.push({
            priority,
            items: priorityBuckets[priority],
          });
        }
      }
    }

    if (uncategorized.length > 0) {
      groupedRecommendations.push({
        priority: "OTHER",
        items: uncategorized,
      });
    }
  }

  return (
    <div className={`flex flex-col gap-5 w-full ${className}`}>
      {/* Advisory Troubleshooting Recommendations Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        {/* Panel Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="healing" size={18} className="text-[#1F5A43]" />
            <div className="flex items-center gap-2">
              <h3 className="text-[14px] font-semibold text-[#171A18]">
                Troubleshooting Recommendations
              </h3>
              <span className="font-mono text-[11px] text-[#5F6360]">
                ({incident.primary_service || "Cluster Telemetry"})
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {recommendations && (
              <>
                <StatusBadge
                  label={recommendations.telemetry_classification}
                  variant={getClassificationVariant(recommendations.telemetry_classification)}
                />
                <StatusBadge
                  label={`${recommendations.total} ITEM${recommendations.total === 1 ? "" : "S"}`}
                  variant="neutral"
                />
              </>
            )}
            <StatusBadge label="ADVISORY ONLY" variant="neutral" />
          </div>
        </div>

        {/* Invariant Banner */}
        <div className="p-2.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center gap-2 text-[12px] text-[#5F6360]">
          <Icon name="info" size={16} className="text-[#1F5A43] shrink-0" />
          <span>
            Advisory diagnostic guidance only. RootLens performs zero autonomous remediation or infrastructure mutations (<strong className="text-[#171A18]">is_root_cause_confirmed: false</strong>).
          </span>
        </div>

        {/* Loading State */}
        {isRecommendationsLoading && (
          <div
            className="p-8 flex flex-col items-center justify-center gap-3 text-center"
            role="status"
            aria-live="polite"
          >
            <div className="w-6 h-6 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
            <span className="text-[13px] text-[#5F6360]">
              Loading troubleshooting recommendations...
            </span>
          </div>
        )}

        {/* Error State */}
        {!isRecommendationsLoading && recommendationsError && (
          <div
            className="p-4 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] flex flex-col gap-2.5"
            role="alert"
          >
            <div className="flex items-center gap-2 text-[#A34B4B] text-[13px] font-medium">
              <Icon name="error" size={16} />
              <span>Failed to load recommendations</span>
            </div>
            <p className="text-[12px] text-[#5F6360]">{recommendationsError}</p>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => onFetchRecommendations()}
                disabled={isRecommendationsLoading || isGeneratingRecommendations}
                className="px-3 py-1.5 rounded-md bg-white border border-[#D8D5CD] text-[#171A18] text-[12px] font-medium hover:bg-[#F5F3EE] transition-colors cursor-pointer disabled:opacity-50"
              >
                Retry Load
              </button>
              <button
                type="button"
                onClick={() => onGenerateRecommendations(false)}
                disabled={isRecommendationsLoading || isGeneratingRecommendations}
                className="px-3 py-1.5 rounded-md bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] transition-colors cursor-pointer disabled:opacity-50"
              >
                Generate Fresh
              </button>
            </div>
          </div>
        )}

        {/* Empty State */}
        {!isRecommendationsLoading &&
          !recommendationsError &&
          (!recommendations || recommendations.items.length === 0) && (
            <div className="p-8 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col items-center justify-center text-center gap-3">
              <div className="w-10 h-10 rounded-full bg-white border border-[#D8D5CD] flex items-center justify-center text-[#5F6360]">
                <Icon name="list_alt" size={20} />
              </div>
              <div className="flex flex-col gap-1">
                <h5 className="text-[14px] font-semibold text-[#171A18]">
                  No Recommendations Available
                </h5>
                <p className="text-[12px] text-[#5F6360] max-w-sm">
                  No advisory recommendations have been generated for this incident yet.
                </p>
              </div>
              <button
                type="button"
                onClick={() => onGenerateRecommendations(false)}
                disabled={isRecommendationsLoading || isGeneratingRecommendations}
                className="mt-1 flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {isGeneratingRecommendations ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    <span>Generating Recommendations...</span>
                  </>
                ) : (
                  <>
                    <Icon name="auto_awesome" size={14} />
                    <span>Generate Recommendations</span>
                  </>
                )}
              </button>
            </div>
          )}

        {/* Recommendations Items List (Grouped by Priority) */}
        {!isRecommendationsLoading &&
          !recommendationsError &&
          recommendations &&
          recommendations.items.length > 0 && (
            <div className="flex flex-col gap-6">
              {groupedRecommendations.map((group) => (
                <div key={group.priority} className="flex flex-col gap-3">
                  {/* Group Header */}
                  <div className="flex items-center gap-2 border-b border-[#D8D5CD] pb-1.5">
                    <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                      {group.priority} Priority ({group.items.length})
                    </span>
                  </div>

                  {/* Recommendation Cards */}
                  <div className="flex flex-col gap-3">
                    {group.items.map((rec) => (
                      <div
                        key={rec.id}
                        className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col gap-2.5"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-[11px] font-semibold text-[#5F6360]">
                              #{rec.sequence_order + 1}
                            </span>
                            <StatusBadge
                              label={rec.action_type.replace(/_/g, " ")}
                              variant={getActionTypeVariant(rec.action_type)}
                            />
                            <StatusBadge
                              label={rec.priority}
                              variant={getPriorityVariant(rec.priority)}
                            />
                          </div>
                          <span className="font-mono text-[10px] text-[#5F6360]">
                            {formatUtc(rec.created_at)}
                          </span>
                        </div>

                        <h5 className="text-[13px] font-semibold text-[#171A18]">
                          {rec.title}
                        </h5>

                        <p className="text-[12px] text-[#5F6360] leading-relaxed whitespace-pre-wrap">
                          {rec.description}
                        </p>

                        {/* Evidence Citations Section */}
                        {rec.evidence_citations && rec.evidence_citations.length > 0 && (
                          <div className="flex flex-col gap-1 pt-0.5">
                            <span className="text-[10px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1">
                              <Icon name="verified" size={12} className="text-[#1F5A43]" />
                              Supporting Evidence ({rec.evidence_citations.length})
                            </span>
                            <div className="flex flex-wrap items-center gap-1.5">
                              {rec.evidence_citations.map((cit, cIdx) => {
                                const isClickable = Boolean(cit.reference_id && cit.reference_id.trim());
                                const isLog = cit.reference_type?.toUpperCase() === "LOG";
                                const isTrace = cit.reference_type?.toUpperCase() === "TRACE";

                                const chip = (
                                  <div
                                    className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-[#D8D5CD] font-mono text-[11px] text-[#171A18] shadow-2xs hover:border-[#1F5A43] transition-colors"
                                    title={`${cit.reference_type} Reference: ${cit.reference_id} (${cit.service_name || "Unknown Service"})`}
                                  >
                                    <span className="text-[10px] font-semibold text-[#1F5A43]">{cit.reference_type}</span>
                                    <span className="text-[#5F6360]">:</span>
                                    <span className="text-[#5F6360] max-w-[100px] truncate">{cit.service_name || "cluster"}</span>
                                    <span className="text-[#5F6360]">/</span>
                                    <span className="font-medium max-w-[120px] truncate">{cit.reference_id}</span>
                                    {(isLog || isTrace) && (
                                      <Icon name="open_in_new" size={11} className="text-[#5F6360] shrink-0" />
                                    )}
                                  </div>
                                );

                                if (isClickable && (isLog || isTrace)) {
                                  return (
                                    <Link
                                      key={`${cit.reference_type}-${cit.reference_id}-${cIdx}`}
                                      href={`/logs?q=${encodeURIComponent(cit.reference_id)}`}
                                      className="inline-flex cursor-pointer hover:opacity-85 transition-opacity"
                                      title={`Inspect ${cit.reference_type.toLowerCase()} evidence ${cit.reference_id} in Log Explorer`}
                                    >
                                      {chip}
                                    </Link>
                                  );
                                }

                                return (
                                  <div key={`${cit.reference_type}-${cit.reference_id}-${cIdx}`} className="inline-flex">
                                    {chip}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}

                        {rec.verification_step && (
                          <div className="p-2.5 rounded bg-white border border-[#D8D5CD] flex flex-col gap-1">
                            <span className="text-[10px] font-semibold text-[#1F5A43] uppercase tracking-wider">
                              Verification Step
                            </span>
                            <p className="text-[11.5px] text-[#171A18] leading-relaxed">
                              {rec.verification_step}
                            </p>
                          </div>
                        )}

                        {rec.caveat && (
                          <div className="p-2 rounded bg-[#FEF7E6] border border-[#FDE8B3] flex items-start gap-1.5 text-[11px] text-[#9A7440]">
                            <Icon
                              name="warning"
                              size={13}
                              className="shrink-0 mt-0.5 text-[#9A7440]"
                            />
                            <span>{rec.caveat}</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              {/* Footer Action Bar */}
              <div className="flex items-center justify-between border-t border-[#D8D5CD] pt-3 text-[12px]">
                <span className="text-[11px] text-[#5F6360]">
                  Classification: {recommendations.telemetry_classification}
                </span>
                <button
                  type="button"
                  onClick={() => onGenerateRecommendations(true)}
                  disabled={isRecommendationsLoading || isGeneratingRecommendations}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] text-[#171A18] text-[12px] font-medium hover:bg-[#F5F3EE] disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
                  aria-label="Regenerate troubleshooting recommendations"
                >
                  {isGeneratingRecommendations ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
                      <span>Regenerating...</span>
                    </>
                  ) : (
                    <>
                      <Icon name="refresh" size={14} className="text-[#5F6360]" />
                      <span>Regenerate Recommendations</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
      </div>
    </div>
  );
}

export default RecommendationsTab;
