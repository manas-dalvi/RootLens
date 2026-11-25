"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import type {
  IncidentDetailResponse,
  IncidentInvestigationResponse,
} from "@/lib/api/incidents";

export interface AiInvestigationTabProps {
  incident: IncidentDetailResponse;
  investigationQuery: string;
  setInvestigationQuery: (q: string) => void;
  investigationResult: IncidentInvestigationResponse | null;
  isSubmittingInvestigation: boolean;
  investigationError: string | null;
  investigationValidationMsg: string | null;
  setInvestigationValidationMsg: (msg: string | null) => void;
  onInvestigate: (overrideQuery?: string) => Promise<void> | void;
  onClearInvestigation: () => void;
  className?: string;
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

export function AiInvestigationTab({
  incident,
  investigationQuery,
  setInvestigationQuery,
  investigationResult,
  isSubmittingInvestigation,
  investigationError,
  investigationValidationMsg,
  setInvestigationValidationMsg,
  onInvestigate,
  onClearInvestigation,
  className = "",
}: AiInvestigationTabProps) {
  const suggestedQueries = [
    "Which service showed the earliest observed failure?",
    "What is the evidence sufficiency classification?",
    "Are there distributed traces linking the affected services?",
    "What telemetry gaps or unobserved entities exist?",
  ];

  return (
    <div className={`flex flex-col gap-5 w-full ${className}`}>
      {/* Incident Investigation Q&A Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        {/* Panel Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="psychology_alt" size={18} className="text-[#1F5A43]" />
            <div className="flex items-center gap-2">
              <h3 className="text-[14px] font-semibold text-[#171A18]">
                Incident Investigation Q&A
              </h3>
              <span className="font-mono text-[11px] text-[#5F6360]">
                ({incident.primary_service || "Cluster Telemetry"})
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {investigationResult && (
              <>
                <StatusBadge
                  label={investigationResult.classification}
                  variant={getClassificationVariant(investigationResult.classification)}
                />
                <StatusBadge
                  label={investigationResult.is_grounded ? "GROUNDED" : "UNGROUNDED"}
                  variant={investigationResult.is_grounded ? "healthy" : "warning"}
                />
                <StatusBadge
                  label={`PROVIDER: ${investigationResult.provider_used.toUpperCase()}`}
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
            Stateless inquiry grounded exclusively in verified telemetry. RootLens never confirms causation from correlation (<strong className="text-[#171A18]">is_root_cause_confirmed: false</strong>).
          </span>
        </div>

        {/* Query Input Section */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onInvestigate();
          }}
          className="flex flex-col gap-2.5"
        >
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              <label htmlFor="investigation-query-input">Ask an Investigation Question</label>
              <span
                className={`font-mono text-[10px] ${
                  investigationQuery.length > 1000 ? "text-[#A34B4B]" : "text-[#5F6360]"
                }`}
              >
                {investigationQuery.length}/1000
              </span>
            </div>
            <div className="relative">
              <textarea
                id="investigation-query-input"
                value={investigationQuery}
                onChange={(e) => {
                  setInvestigationQuery(e.target.value);
                  if (investigationValidationMsg) setInvestigationValidationMsg(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onInvestigate();
                  }
                }}
                placeholder="e.g. Which service showed the earliest failure? Are there distributed traces linking payment to auth?"
                rows={3}
                disabled={isSubmittingInvestigation}
                className="w-full p-3 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] placeholder-[#5F6360]/70 focus:outline-none focus:ring-2 focus:ring-[#1F5A43] focus:bg-white resize-y transition-all disabled:opacity-60"
                aria-label="Investigation inquiry question"
              />
            </div>
          </div>

          {/* Validation Message */}
          {investigationValidationMsg && (
            <div className="text-[12px] text-[#A34B4B] flex items-center gap-1.5" role="alert">
              <Icon name="error_outline" size={14} />
              <span>{investigationValidationMsg}</span>
            </div>
          )}

          {/* Submit and Control Buttons */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={isSubmittingInvestigation || investigationQuery.trim().length < 3}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
                aria-label="Submit investigation inquiry"
              >
                {isSubmittingInvestigation ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    <span>Investigating Telemetry...</span>
                  </>
                ) : (
                  <>
                    <Icon name="search" size={14} />
                    <span>Ask Question</span>
                  </>
                )}
              </button>
              {(investigationQuery || investigationResult) && !isSubmittingInvestigation && (
                <button
                  type="button"
                  onClick={onClearInvestigation}
                  className="px-2.5 py-1.5 rounded-lg text-[#5F6360] text-[12px] hover:bg-[#F5F3EE] hover:text-[#171A18] transition-colors cursor-pointer"
                >
                  Clear
                </button>
              )}
            </div>

            <span className="text-[11px] text-[#5F6360] hidden sm:inline">
              Press Enter to submit
            </span>
          </div>
        </form>

        {/* Suggested Questions (when idle and no result) */}
        {!investigationResult && !isSubmittingInvestigation && !investigationError && (
          <div className="flex flex-col gap-2 pt-1 border-t border-[#D8D5CD]">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Suggested Diagnostic Queries
            </span>
            <div className="flex flex-wrap gap-1.5">
              {suggestedQueries.map((sampleQ, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => {
                    setInvestigationQuery(sampleQ);
                    if (investigationValidationMsg) setInvestigationValidationMsg(null);
                  }}
                  className="text-left px-2.5 py-1 rounded-md bg-[#F5F3EE] hover:bg-[#EAE7DF] hover:text-[#1F5A43] border border-[#D8D5CD] text-[11.5px] text-[#5F6360] transition-colors cursor-pointer"
                >
                  {sampleQ}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Submitting Progress Indicator */}
        {isSubmittingInvestigation && (
          <div
            className="p-6 flex flex-col items-center justify-center gap-3 text-center border-t border-[#D8D5CD]"
            role="status"
            aria-live="polite"
          >
            <div className="w-6 h-6 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
            <span className="text-[13px] text-[#5F6360]">
              Evaluating query against verified telemetry records...
            </span>
          </div>
        )}

        {/* Error State */}
        {!isSubmittingInvestigation && investigationError && (
          <div
            className="p-4 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] flex flex-col gap-2.5"
            role="alert"
          >
            <div className="flex items-center gap-2 text-[#A34B4B] text-[13px] font-medium">
              <Icon name="error" size={16} />
              <span>Investigation Query Failed</span>
            </div>
            <p className="text-[12px] text-[#5F6360]">{investigationError}</p>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => onInvestigate()}
                disabled={isSubmittingInvestigation || investigationQuery.trim().length < 3}
                className="px-3 py-1.5 rounded-md bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] transition-colors cursor-pointer"
              >
                Retry Query
              </button>
            </div>
          </div>
        )}

        {/* Answer / Result State */}
        {!isSubmittingInvestigation && investigationResult && (
          <div className="flex flex-col gap-3.5 pt-3 border-t border-[#D8D5CD]">
            {/* Original Question Echo */}
            <div className="flex items-start gap-2 p-2.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[12px]">
              <Icon name="help_outline" size={16} className="text-[#1F5A43] shrink-0 mt-0.5" />
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Inquiry
                </span>
                <span className="font-medium text-[#171A18]">{investigationResult.query}</span>
              </div>
            </div>

            {/* Fallback Warning if engaged */}
            {investigationResult.provider_used === "deterministic" && (
              <div className="p-2.5 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] flex items-start gap-2 text-[12px] text-[#9A7440]">
                <Icon name="warning" size={16} className="text-[#9A7440] shrink-0 mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <span className="font-semibold">Deterministic Policy Verification</span>
                  <span>Answer generated deterministically from verified structured evidence.</span>
                </div>
              </div>
            )}

            {/* Diagnostic Answer */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Grounded Diagnostic Response
                </span>
                <span className="font-mono text-[10px] text-[#5F6360]">
                  {investigationResult.model_used}
                </span>
              </div>
              <div className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap">
                {investigationResult.answer}
              </div>
            </div>

            {/* Unobserved Entities / Telemetry Gaps */}
            {investigationResult.unobserved_entities_mentioned &&
              investigationResult.unobserved_entities_mentioned.length > 0 && (
                <div className="p-3 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] flex flex-col gap-1 text-[12px] text-[#9A7440]">
                  <div className="flex items-center gap-1.5 font-semibold">
                    <Icon name="warning" size={15} className="text-[#9A7440]" />
                    <span>Unobserved Entities (Telemetry Gap)</span>
                  </div>
                  <p className="text-[11.5px] leading-relaxed">
                    Zero telemetry found in incident evidence bundle for:{" "}
                    <strong className="font-mono">
                      {investigationResult.unobserved_entities_mentioned.join(", ")}
                    </strong>
                  </p>
                </div>
              )}

            {/* Cited Evidence References */}
            {investigationResult.cited_references && investigationResult.cited_references.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Verified Evidence Citations ({investigationResult.cited_references.length})
                </span>
                <div className="flex flex-wrap gap-2">
                  {investigationResult.cited_references.map((ref, idx) => {
                    const isLog = ref.reference_type === "LOG";
                    const chipContent = (
                      <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#F5F3EE] border border-[#D8D5CD] font-mono text-[11px] text-[#171A18]">
                        <span className="text-[10px] font-semibold text-[#1F5A43]">
                          {ref.reference_type}
                        </span>
                        <span className="text-[#D8D5CD]">:</span>
                        <span className="text-[#5F6360]">{ref.service_name}</span>
                        <span className="text-[#D8D5CD]">/</span>
                        <span
                          className="font-medium truncate max-w-[140px]"
                          title={ref.reference_id}
                        >
                          {ref.reference_id}
                        </span>
                        {isLog && (
                          <Icon name="open_in_new" size={11} className="text-[#5F6360]" />
                        )}
                      </div>
                    );

                    return isLog ? (
                      <Link
                        key={`${ref.reference_type}-${ref.reference_id}-${idx}`}
                        href={`/logs?q=${encodeURIComponent(ref.reference_id)}`}
                        className="hover:opacity-80 transition-opacity"
                        title={`Inspect OpenSearch log ${ref.reference_id}`}
                      >
                        {chipContent}
                      </Link>
                    ) : (
                      <div key={`${ref.reference_type}-${ref.reference_id}-${idx}`}>
                        {chipContent}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="text-[11px] text-[#5F6360]">
                No specific individual log or trace documents were cited in this answer.
              </div>
            )}

            {/* Metadata and Safety Footer */}
            <div className="flex items-center justify-between border-t border-[#D8D5CD] pt-2.5 text-[11px] text-[#5F6360]">
              <span>Causality: unconfirmed (is_root_cause_confirmed: false)</span>
              <span>Provider: {investigationResult.provider_used}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default AiInvestigationTab;
