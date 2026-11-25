"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  IncidentDetailResponse,
  IncidentExplanationResponse,
  RCACandidateFindingResponse,
  RCACitationItem,
  RCAClaimMapping,
  RCAHypothesisDecisionsSummaryResponse,
  fetchHypothesisDecisions,
  submitHypothesisDecision,
} from "@/lib/api/incidents";

export interface OverviewRcaTabProps {
  incident: IncidentDetailResponse;
  explanation: IncidentExplanationResponse | null;
  isExplanationLoading: boolean;
  isRequestingLlm: boolean;
  explanationError: string | null;
  isLlmRequested: boolean;
  onLoadExplanation: (useLlm: boolean) => void | Promise<void>;
  className?: string;
}

export function getClassificationVariant(classification?: string): StatusVariant {
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

export function renderRcaCitationChip(cit: RCACitationItem, key: string | number) {
  const isClickable = Boolean(cit.log_id && cit.log_id.trim());
  const formattedTs = cit.timestamp
    ? new Date(cit.timestamp).toISOString().replace("T", " ").substring(0, 19) + " UTC"
    : null;

  const chipContent = (
    <div
      className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-[#D8D5CD] font-mono text-[11px] text-[#171A18] shadow-2xs hover:border-[#1F5A43] transition-colors"
      title={`Inspect log record ${cit.log_id} (${cit.service_name}) in Log Explorer`}
    >
      <span className="text-[10px] font-semibold text-[#1F5A43] uppercase">{cit.evidence_type}</span>
      <span className="text-[#D8D5CD]">/</span>
      <span className="text-[#5F6360] font-medium max-w-[120px] truncate">{cit.service_name}</span>
      <span className="text-[#D8D5CD]">/</span>
      <span className="font-semibold text-[#171A18] max-w-[140px] truncate">{cit.log_id}</span>
      {isClickable && <Icon name="open_in_new" size={11} className="text-[#5F6360] shrink-0" />}
    </div>
  );

  return (
    <div key={key} className="flex flex-col gap-1.5 p-2.5 rounded-lg bg-white border border-[#E8E6DF] shadow-2xs">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        {isClickable ? (
          <Link
            href={`/logs?q=${encodeURIComponent(cit.log_id)}`}
            className="inline-flex cursor-pointer hover:opacity-85 transition-opacity"
            title={`Inspect log record ${cit.log_id} in Log Explorer`}
          >
            {chipContent}
          </Link>
        ) : (
          <div className="inline-flex">{chipContent}</div>
        )}
        {formattedTs && (
          <span className="font-mono text-[10px] text-[#5F6360]">
            {formattedTs}
          </span>
        )}
      </div>

      {cit.log_snippet && (
        <div className="font-mono text-[11px] text-[#171A18] bg-[#F5F3EE] p-2 rounded border border-[#E8E6DF] whitespace-pre-wrap break-all max-h-24 overflow-y-auto leading-relaxed">
          {cit.log_snippet}
        </div>
      )}

      {cit.support_explanation && (
        <p className="text-[11px] text-[#5F6360] leading-snug">
          <strong className="text-[#1F5A43] font-semibold">Supporting Rationale:</strong>{" "}
          {cit.support_explanation}
        </p>
      )}
    </div>
  );
}

interface ReviewModalState {
  hypothesisId: string;
  hypothesisText: string;
  isSupported: boolean;
  action: "CONFIRM" | "REJECT";
}

export function OverviewRcaTab({
  incident,
  explanation,
  isExplanationLoading,
  isRequestingLlm,
  explanationError,
  isLlmRequested,
  onLoadExplanation,
  className = "",
}: OverviewRcaTabProps) {
  const assessment = incident.assessment;
  const rcaSynthesis = incident.rca_synthesis || assessment?.rca_synthesis;
  const allClaimCitations: RCAClaimMapping[] = rcaSynthesis?.claim_citations || [];
  const factClaims = allClaimCitations.filter((c) => c.claim_type === "OBSERVED_FACT");

  const classification =
    rcaSynthesis?.classification ||
    assessment?.classification ||
    "INSUFFICIENT";

  const hasFormulatedHypothesis = Boolean(
    rcaSynthesis?.primary_hypothesis || incident.probable_cause
  );
  const primaryHypothesis =
    rcaSynthesis?.primary_hypothesis ||
    incident.probable_cause ||
    "No root-cause hypothesis is currently available from the collected evidence.";

  const observedFacts =
    rcaSynthesis?.observed_facts ||
    assessment?.observed_facts ||
    [];

  const findings: RCACandidateFindingResponse[] =
    rcaSynthesis?.findings || [];

  // Decisions State
  const [decisionsSummary, setDecisionsSummary] =
    useState<RCAHypothesisDecisionsSummaryResponse | null>(null);
  const [modalState, setModalState] = useState<ReviewModalState | null>(null);
  const [operatorNotes, setOperatorNotes] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Load decision status asynchronously
  useEffect(() => {
    let isCancelled = false;
    const incidentId = incident.id;
    if (incidentId) {
      fetchHypothesisDecisions(incidentId)
        .then((data) => {
          if (!isCancelled) {
            setDecisionsSummary(data);
          }
        })
        .catch(() => {
          // Graceful fallback for offline mode
        });
    }
    return () => {
      isCancelled = true;
    };
  }, [incident.id]);

  const refreshDecisions = async () => {
    if (!incident.id) return;
    try {
      const data = await fetchHypothesisDecisions(incident.id);
      setDecisionsSummary(data);
    } catch {
      // Graceful fallback
    }
  };

  const openReviewModal = (
    hypothesisId: string,
    hypothesisText: string,
    isSupported: boolean,
    action: "CONFIRM" | "REJECT"
  ) => {
    setModalState({ hypothesisId, hypothesisText, isSupported, action });
    setOperatorNotes("");
    setSubmitError(null);
  };

  const closeReviewModal = () => {
    setModalState(null);
    setOperatorNotes("");
    setSubmitError(null);
  };

  const handleDecisionSubmit = async () => {
    if (!modalState) return;
    if (operatorNotes.trim().length < 10) {
      setSubmitError("Operator notes must be at least 10 characters long.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await submitHypothesisDecision(incident.id, modalState.hypothesisId, {
        decision: modalState.action,
        notes: operatorNotes.trim(),
        hypothesis_text: modalState.hypothesisText,
      });
      await refreshDecisions();
      closeReviewModal();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSubmitError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderClaimCard = (claim: RCAClaimMapping, key: string | number) => {
    const isFact = claim.claim_type === "OBSERVED_FACT";
    const activeDecision = decisionsSummary?.active_decisions[claim.claim_id];
    const isConfirmed = activeDecision?.decision === "CONFIRM";
    const isRejected = activeDecision?.decision === "REJECT";

    return (
      <div
        key={key}
        className={`p-3.5 rounded-lg border flex flex-col gap-2.5 text-[12px] transition-colors ${
          isConfirmed
            ? "bg-[#F3FAF6] border-[#A8DFC1]"
            : isRejected
            ? "bg-[#FEF5F5] border-[#F8C8C8]"
            : "bg-[#FAF9F5] border-[#D8D5CD]"
        }`}
      >
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-1.5 flex-wrap">
            {claim.is_supported ? (
              isFact ? (
                <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded bg-[#E8F3EE] text-[#1F5A43] border border-[#C2E0D1]">
                  OBSERVED FACT - SUPPORTED
                </span>
              ) : isConfirmed ? (
                <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded bg-[#E8F3EE] text-[#1F5A43] border border-[#C2E0D1] flex items-center gap-1">
                  <Icon name="verified" size={11} className="text-[#1F5A43]" />
                  OPERATOR CONFIRMED ROOT CAUSE
                </span>
              ) : isRejected ? (
                <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded bg-[#FEECEB] text-[#A34B4B] border border-[#FCD3CE] flex items-center gap-1">
                  <Icon name="cancel" size={11} className="text-[#A34B4B]" />
                  OPERATOR REJECTED
                </span>
              ) : (
                <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded bg-[#FEF7E6] text-[#9A7440] border border-[#FDE8B3] flex items-center gap-1">
                  <Icon name="pending" size={11} className="text-[#9A7440]" />
                  AWAITING OPERATOR REVIEW
                </span>
              )
            ) : (
              <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded bg-[#F5F3EE] text-[#5F6360] border border-[#D8D5CD]">
                INSUFFICIENT TELEMETRY
              </span>
            )}
            <span className="font-mono text-[10px] text-[#5F6360]">({claim.claim_id})</span>
          </div>

          <div className="flex items-center gap-2">
            {!isFact && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={!claim.is_supported}
                  onClick={() =>
                    openReviewModal(claim.claim_id, claim.claim_text, claim.is_supported, "CONFIRM")
                  }
                  title={
                    claim.is_supported
                      ? "Promote hypothesis to confirmed root cause"
                      : "Cannot confirm unsupported hypothesis without evidence citations"
                  }
                  className={`text-[11px] font-semibold px-2 py-0.5 rounded border transition-colors ${
                    claim.is_supported
                      ? "bg-white text-[#1F5A43] border-[#1F5A43] hover:bg-[#E8F3EE] cursor-pointer"
                      : "bg-[#F5F3EE] text-[#A0A2A0] border-[#D8D5CD] cursor-not-allowed"
                  }`}
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() =>
                    openReviewModal(claim.claim_id, claim.claim_text, claim.is_supported, "REJECT")
                  }
                  title="Reject hypothesis with operator justification"
                  className="text-[11px] font-semibold px-2 py-0.5 rounded border bg-white text-[#A34B4B] border-[#A34B4B] hover:bg-[#FEECEB] transition-colors cursor-pointer"
                >
                  Reject
                </button>
              </div>
            )}
          </div>
        </div>

        <p className="text-[#171A18] font-medium leading-relaxed">{claim.claim_text}</p>

        {/* Supporting citations */}
        {claim.citations && claim.citations.length > 0 && (
          <div className="flex flex-col gap-1.5 pt-1">
            <span className="text-[10px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1">
              <Icon name="verified" size={12} className="text-[#1F5A43]" />
              Supporting Log Citations ({claim.citations.length})
            </span>
            <div className="flex flex-col gap-1.5">
              {claim.citations.map((cit, cIdx) =>
                renderRcaCitationChip(cit, `${claim.claim_id}-cit-${cIdx}`)
              )}
            </div>
          </div>
        )}

        {/* Unsupported reason if unsupported */}
        {!claim.is_supported && claim.unsupported_reason && (
          <div className="p-2 rounded bg-[#F5F3EE] border border-[#D8D5CD] text-[11px] text-[#5F6360] italic flex items-center gap-1.5">
            <Icon name="info" size={13} className="shrink-0 text-[#5F6360]" />
            <span>{claim.unsupported_reason}</span>
          </div>
        )}
      </div>
    );
  };

  const isIncidentConfirmed = Boolean(
    decisionsSummary?.is_root_cause_confirmed || incident.is_root_cause_confirmed
  );

  return (
    <div className={`flex flex-col gap-5 w-full ${className}`}>
      {/* 1. RCA Synthesis & Primary Hypothesis Card */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="psychology" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              RCA Synthesis & Working Hypothesis
            </h3>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge
              label={`TELEMETRY: ${classification}`}
              variant={getClassificationVariant(classification)}
            />
            <span className="font-mono text-[11px] px-2 py-0.5 rounded font-medium bg-[#F5F3EE] text-[#5F6360] border border-[#D8D5CD]">
              CONFIDENCE: {(incident.confidence_score * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        {/* Operator Confirmation Invariant Status Banner */}
        <div
          className={`p-3 rounded-lg border flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-[12px] ${
            isIncidentConfirmed
              ? "bg-[#E8F3EE] border-[#A8DFC1] text-[#1F5A43]"
              : "bg-[#F5F3EE] border-[#D8D5CD] text-[#5F6360]"
          }`}
        >
          <div className="flex items-center gap-2 min-w-0">
            <Icon
              name={isIncidentConfirmed ? "verified" : "verified_user"}
              size={17}
              className={isIncidentConfirmed ? "text-[#1F5A43] shrink-0" : "text-[#5F6360] shrink-0"}
            />
            <span className="truncate">
              {isIncidentConfirmed
                ? "Human-in-the-Loop: Root cause hypothesis has been formally CONFIRMED by an authorized operator."
                : "Human-in-the-Loop Invariant: AI hypotheses never become confirmed root causes automatically."}
            </span>
          </div>
          <span
            className={`font-mono text-[11px] font-semibold px-2 py-0.5 rounded shrink-0 ${
              isIncidentConfirmed
                ? "bg-white text-[#1F5A43] border border-[#A8DFC1]"
                : "bg-white text-[#5F6360] border border-[#D8D5CD]"
            }`}
          >
            is_root_cause_confirmed: {String(isIncidentConfirmed)}
          </span>
        </div>

        {/* Primary Hypothesis Box */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Primary Working Hypothesis
            </span>
            {hasFormulatedHypothesis && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => openReviewModal("primary", primaryHypothesis, true, "CONFIRM")}
                  className="text-[11px] font-semibold px-2.5 py-1 rounded bg-[#1F5A43] text-white hover:bg-[#184835] transition-colors cursor-pointer flex items-center gap-1 shadow-2xs"
                >
                  <Icon name="check" size={12} className="text-white" />
                  Confirm Root Cause
                </button>
                <button
                  type="button"
                  onClick={() => openReviewModal("primary", primaryHypothesis, true, "REJECT")}
                  className="text-[11px] font-semibold px-2.5 py-1 rounded bg-white text-[#A34B4B] border border-[#A34B4B] hover:bg-[#FEECEB] transition-colors cursor-pointer flex items-center gap-1"
                >
                  <Icon name="close" size={12} className="text-[#A34B4B]" />
                  Reject Hypothesis
                </button>
              </div>
            )}
          </div>

          {hasFormulatedHypothesis ? (
            <div className="p-3.5 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] text-[13px] text-[#171A18] leading-relaxed flex flex-col gap-2.5">
              <div className="flex items-start gap-2.5">
                <Icon name="lightbulb" size={17} className="text-[#9A7440] shrink-0 mt-0.5" />
                <div className="flex flex-col gap-1 w-full">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="font-semibold text-[#9A7440]">
                      Formulated Root Cause Hypothesis
                    </span>
                    {decisionsSummary?.active_decisions["primary"] && (
                      <span
                        className={`text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${
                          decisionsSummary.active_decisions["primary"].decision === "CONFIRM"
                            ? "bg-[#E8F3EE] text-[#1F5A43] border-[#A8DFC1]"
                            : "bg-[#FEECEB] text-[#A34B4B] border-[#FCD3CE]"
                        }`}
                      >
                        REVIEW: {decisionsSummary.active_decisions["primary"].decision} (by{" "}
                        {decisionsSummary.active_decisions["primary"].operator_name})
                      </span>
                    )}
                  </div>
                  <p className="text-[#171A18]">{primaryHypothesis}</p>

                  {rcaSynthesis?.suspected_failure_mechanism && (
                    <div className="mt-2 p-2.5 rounded bg-white border border-[#FDE8B3] text-[12px] flex flex-col gap-1">
                      <span className="font-semibold text-[#9A7440] uppercase text-[10px] tracking-wider">
                        Suspected Failure Mechanism
                      </span>
                      <p className="text-[#171A18] font-medium leading-relaxed">
                        {rcaSynthesis.suspected_failure_mechanism}
                      </p>
                    </div>
                  )}

                  {(rcaSynthesis?.potential_root_cause_service || (rcaSynthesis?.downstream_symptoms && rcaSynthesis.downstream_symptoms.length > 0)) && (
                    <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
                      {rcaSynthesis?.potential_root_cause_service && (
                        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-[#D8D5CD] text-[#5F6360]">
                          <span className="font-semibold text-[#1F5A43]">POTENTIAL INITIATING SERVICE:</span>
                          <span className="font-mono text-[#171A18]">{rcaSynthesis.potential_root_cause_service}</span>
                        </div>
                      )}
                      {rcaSynthesis?.downstream_symptoms && rcaSynthesis.downstream_symptoms.length > 0 && (
                        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-white border border-[#D8D5CD] text-[#5F6360]">
                          <span className="font-semibold text-[#9A7440]">DOWNSTREAM SYMPTOMS:</span>
                          <span>{rcaSynthesis.downstream_symptoms.length} symptom(s) observed</span>
                        </div>
                      )}
                    </div>
                  )}

                  {rcaSynthesis?.alternative_explanations && rcaSynthesis.alternative_explanations.length > 0 && (
                    <div className="mt-2 p-2.5 rounded bg-[#FAF9F5] border border-[#E8E6DF] text-[11px] flex flex-col gap-1.5">
                      <span className="font-semibold text-[#5F6360] uppercase text-[10px] tracking-wider flex items-center gap-1">
                        <Icon name="device_hub" size={12} className="text-[#5F6360]" />
                        Alternative Non-Causal Explanations
                      </span>
                      <ul className="flex flex-col gap-1 text-[#5F6360] pl-3 list-disc">
                        {rcaSynthesis.alternative_explanations.map((alt, aIdx) => (
                          <li key={aIdx}>{alt}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>

              {/* Supporting log citations for primary hypothesis */}
              {allClaimCitations
                .filter(
                  (c) =>
                    c.claim_type === "INFERRED_HYPOTHESIS" && c.citations && c.citations.length > 0
                )
                .map((hypClaim, hIdx) => (
                  <div key={hIdx} className="flex flex-col gap-1.5 pt-2 border-t border-[#FDE8B3]">
                    <span className="text-[10px] font-semibold text-[#9A7440] uppercase tracking-wider flex items-center gap-1">
                      <Icon name="verified" size={12} className="text-[#1F5A43]" />
                      Supporting Hypothesis Evidence ({hypClaim.citations.length})
                    </span>
                    <div className="flex flex-col gap-1.5">
                      {hypClaim.citations.map((cit, cIdx) =>
                        renderRcaCitationChip(cit, `primary-hyp-cit-${cIdx}`)
                      )}
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <div className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#5F6360] leading-relaxed">
              <div className="flex items-start gap-2.5">
                <Icon name="help_outline" size={17} className="text-[#5F6360] shrink-0 mt-0.5" />
                <div className="flex flex-col gap-1">
                  <span className="font-semibold text-[#5F6360]">Hypothesis Unavailable</span>
                  <p className="italic text-[#5F6360]">{primaryHypothesis}</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Observed Facts with Fine-Grained Citations */}
        {observedFacts.length > 0 && (
          <div className="flex flex-col gap-2">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Empirical Facts Observed in Telemetry ({observedFacts.length})
            </span>
            {factClaims.length > 0 ? (
              <div className="flex flex-col gap-2">
                {factClaims.map((claim, idx) =>
                  renderClaimCard(claim, claim.claim_id || `fact-claim-${idx}`)
                )}
              </div>
            ) : (
              <ul className="flex flex-col gap-1 text-[12px] text-[#171A18]">
                {observedFacts.map((fact, idx) => (
                  <li key={idx} className="flex items-start gap-2">
                    <span className="text-[#1F5A43] font-bold">•</span>
                    <span>{fact}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Structured Candidate Findings */}
        {findings.length > 0 && (
          <div className="flex flex-col gap-2 pt-1 border-t border-[#D8D5CD]">
            <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
              Evaluated Candidate Findings ({findings.length})
            </span>
            <div className="flex flex-col gap-3">
              {findings.map((finding, idx) => (
                <div
                  key={idx}
                  className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col gap-3 text-[12px]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-[#171A18]">
                      Finding #{idx + 1}: {finding.explanation}
                    </span>
                  </div>

                  <p className="text-[#5F6360] text-[12px] leading-relaxed">
                    <strong className="text-[#171A18]">Rationale:</strong>{" "}
                    {finding.plausibility_rationale}
                  </p>

                  {/* Contradictory signals alert */}
                  {finding.contradictory_signals && finding.contradictory_signals.length > 0 && (
                    <div className="p-2 rounded bg-[#FEECEB] border border-[#FCD3CE] text-[#A34B4B] flex items-start gap-2">
                      <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
                      <div className="flex flex-col gap-0.5">
                        <span className="font-semibold">Contradictory Signals Detected:</span>
                        <span>{finding.contradictory_signals.join("; ")}</span>
                      </div>
                    </div>
                  )}

                  {/* Fine-Grained Claim Citations for this finding */}
                  {finding.claim_citations && finding.claim_citations.length > 0 && (
                    <div className="flex flex-col gap-2 pt-2 border-t border-[#E8E6DF]">
                      <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                        Fine-Grained Claim-Level Citations ({finding.claim_citations.length})
                      </span>
                      <div className="flex flex-col gap-2">
                        {finding.claim_citations.map((claim, cIdx) =>
                          renderClaimCard(claim, claim.claim_id || `finding-${idx}-claim-${cIdx}`)
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 2. Immutable Operator Decision Audit History Section */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        <div className="flex items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="history_edu" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              Operator Review & Confirmation Audit History
            </h3>
          </div>
          <span className="font-mono text-[11px] text-[#5F6360]">
            {decisionsSummary?.history?.length || 0} decision record(s)
          </span>
        </div>

        {decisionsSummary?.history && decisionsSummary.history.length > 0 ? (
          <div className="flex flex-col gap-3">
            {decisionsSummary.history.map((rec) => (
              <div
                key={rec.id}
                className="p-3.5 rounded-lg bg-[#FAF9F5] border border-[#D8D5CD] flex flex-col gap-2 text-[12px]"
              >
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2">
                    <span
                      className={`font-mono text-[10px] font-semibold px-2 py-0.5 rounded border ${
                        rec.decision === "CONFIRM"
                          ? "bg-[#E8F3EE] text-[#1F5A43] border-[#A8DFC1]"
                          : "bg-[#FEECEB] text-[#A34B4B] border-[#FCD3CE]"
                      }`}
                    >
                      {rec.decision === "CONFIRM" ? "CONFIRMED ROOT CAUSE" : "REJECTED HYPOTHESIS"}
                    </span>
                    <span className="font-mono text-[11px] text-[#5F6360]">
                      ({rec.hypothesis_id})
                    </span>
                    {rec.previous_decision && (
                      <span className="text-[10px] text-[#5F6360] italic">
                        (Prior: {rec.previous_decision})
                      </span>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-[#5F6360]">
                    {new Date(rec.decided_at).toISOString().replace("T", " ").substring(0, 19)} UTC
                  </span>
                </div>

                <p className="text-[#171A18] font-medium leading-relaxed italic">
                  &ldquo;{rec.notes}&rdquo;
                </p>

                <div className="flex items-center justify-between gap-2 pt-2 border-t border-[#E8E6DF] text-[11px] text-[#5F6360] flex-wrap">
                  <span>
                    Decided by:{" "}
                    <strong className="text-[#171A18]">{rec.operator_name}</strong> (
                    {rec.operator_email})
                  </span>
                  <span>
                    Citations snapshot:{" "}
                    <strong className="text-[#171A18]">
                      {rec.evidence_citations?.length || 0}
                    </strong>{" "}
                    telemetry log(s)
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-4 rounded-lg bg-[#FAF9F5] border border-dashed border-[#D8D5CD] text-[12px] text-[#5F6360] text-center">
            No operator review decisions recorded yet. Authorized operators may confirm or reject
            hypotheses above.
          </div>
        )}
      </div>

      {/* 3. AI Diagnostic Explanation Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="auto_awesome" size={18} className="text-[#1F5A43]" />
            <h3 className="text-[14px] font-semibold text-[#171A18]">
              AI Diagnostic Explanation
            </h3>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {explanation && (
              <>
                <StatusBadge
                  label={explanation.classification}
                  variant={getClassificationVariant(explanation.classification)}
                />
                <StatusBadge
                  label={`PROVIDER: ${explanation.provider_used.toUpperCase()}`}
                  variant="neutral"
                />
              </>
            )}
            <StatusBadge label="ADVISORY ONLY" variant="neutral" />
          </div>
        </div>

        {/* Loading State */}
        {isExplanationLoading && (
          <div className="p-6 flex flex-col items-center justify-center gap-3 text-center" role="status" aria-live="polite">
            <div className="w-6 h-6 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
            <span className="text-[13px] text-[#5F6360]">Loading evidence-grounded explanation...</span>
          </div>
        )}

        {/* Error State */}
        {!isExplanationLoading && explanationError && (
          <div className="p-4 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] flex flex-col gap-2.5" role="alert">
            <div className="flex items-center gap-2 text-[#A34B4B] text-[13px] font-medium">
              <Icon name="error" size={16} />
              <span>Failed to load explanation</span>
            </div>
            <p className="text-[12px] text-[#5F6360]">{explanationError}</p>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => onLoadExplanation(false)}
                disabled={isRequestingLlm}
                className="px-3 py-1.5 rounded-md bg-white border border-[#D8D5CD] text-[#171A18] text-[12px] font-medium hover:bg-[#F5F3EE] transition-colors cursor-pointer"
              >
                Retry Deterministic
              </button>
              <button
                type="button"
                onClick={() => onLoadExplanation(true)}
                disabled={isRequestingLlm}
                className="px-3 py-1.5 rounded-md bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] transition-colors cursor-pointer"
              >
                Retry with LLM
              </button>
            </div>
          </div>
        )}

        {/* Content State */}
        {!isExplanationLoading && explanation && (
          <div className="flex flex-col gap-4">
            {isLlmRequested && explanation.provider_used === "deterministic" && (
              <div className="p-3 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] flex items-start gap-2 text-[12px] text-[#9A7440]">
                <Icon name="warning" size={16} className="text-[#9A7440] shrink-0 mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <span className="font-semibold">Deterministic Fallback Engaged</span>
                  <span>
                    LLM service was requested, but offline/fallback mode returned the verified deterministic synthesis.
                  </span>
                </div>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                Diagnostic Narrative
              </span>
              <div className="p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap">
                {explanation.explanation}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                Diagnostic Uncertainty & Bounds
              </span>
              <p className="text-[12px] text-[#5F6360] leading-relaxed">
                {explanation.uncertainty_statement}
              </p>
            </div>

            {explanation.missing_telemetry && explanation.missing_telemetry.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Missing Telemetry Signals ({explanation.missing_telemetry.length})
                </span>
                <ul className="flex flex-col gap-1 text-[12px] text-[#5F6360]">
                  {explanation.missing_telemetry.map((item, idx) => (
                    <li key={idx} className="flex items-start gap-2">
                      <span className="text-[#5F6360]">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {explanation.evidence_references && explanation.evidence_references.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  Cited Evidence References ({explanation.evidence_references.length})
                </span>
                <div className="flex flex-wrap gap-2">
                  {explanation.evidence_references.map((ref, idx) => {
                    const isLog = ref.reference_type === "LOG";
                    const chipContent = (
                      <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#F5F3EE] border border-[#D8D5CD] font-mono text-[11px] text-[#171A18]">
                        <span className="text-[10px] font-semibold text-[#1F5A43]">{ref.reference_type}</span>
                        <span className="text-[#5F6360]">:</span>
                        <span className="text-[#5F6360]">{ref.service_name}</span>
                        <span className="text-[#5F6360]">/</span>
                        <span className="font-medium truncate max-w-[140px]" title={ref.reference_id}>
                          {ref.reference_id}
                        </span>
                        {isLog && <Icon name="open_in_new" size={11} className="text-[#5F6360]" />}
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
            )}
          </div>
        )}
      </div>

      {/* 4. Review Modal Dialog */}
      {modalState && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="w-full max-w-lg rounded-xl bg-white border border-[#D8D5CD] shadow-xl p-5 flex flex-col gap-4 animate-in fade-in duration-150">
            <div className="flex items-center justify-between gap-2 border-b border-[#D8D5CD] pb-3">
              <div className="flex items-center gap-2">
                <Icon
                  name={modalState.action === "CONFIRM" ? "verified" : "cancel"}
                  size={18}
                  className={
                    modalState.action === "CONFIRM" ? "text-[#1F5A43]" : "text-[#A34B4B]"
                  }
                />
                <h4 className="text-[14px] font-semibold text-[#171A18]">
                  {modalState.action === "CONFIRM"
                    ? "Confirm Hypothesis as Root Cause"
                    : "Reject Hypothesis"}
                </h4>
              </div>
              <button
                type="button"
                onClick={closeReviewModal}
                className="text-[#5F6360] hover:text-[#171A18] p-1 rounded cursor-pointer"
              >
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="flex flex-col gap-1.5 bg-[#FAF9F5] p-3 rounded-lg border border-[#D8D5CD] text-[12px]">
              <span className="text-[10px] font-semibold text-[#5F6360] uppercase tracking-wider">
                Hypothesis Target ({modalState.hypothesisId})
              </span>
              <p className="text-[#171A18] font-medium leading-relaxed">
                {modalState.hypothesisText}
              </p>
            </div>

            {/* Invariant Note */}
            <div className="text-[11px] text-[#5F6360] flex items-center gap-1.5">
              <Icon name="info" size={14} className="text-[#1F5A43] shrink-0" />
              <span>
                {modalState.action === "CONFIRM"
                  ? "Confirmation formally updates the incident root cause and is grounded in verified telemetry citations."
                  : "Rejection updates the immutable audit history and retracts any prior confirmation."}
              </span>
            </div>

            {/* Operator Notes Input */}
            <div className="flex flex-col gap-1">
              <label className="text-[12px] font-semibold text-[#171A18] flex items-center justify-between">
                <span>
                  Operator Justification / Notes <span className="text-[#A34B4B]">*</span>
                </span>
                <span className="font-mono text-[10px] text-[#5F6360]">
                  {operatorNotes.length}/10 min chars
                </span>
              </label>
              <textarea
                value={operatorNotes}
                onChange={(e) => setOperatorNotes(e.target.value)}
                placeholder="Detail verification rationale, observed metrics, or reasons for rejection (minimum 10 characters)..."
                rows={4}
                className="w-full p-2.5 rounded-lg border border-[#D8D5CD] text-[12px] text-[#171A18] placeholder:text-[#A0A2A0] focus:outline-none focus:border-[#1F5A43]"
              />
            </div>

            {submitError && (
              <div className="p-2.5 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] text-[11px] text-[#A34B4B] flex items-center gap-1.5">
                <Icon name="warning" size={14} className="shrink-0 text-[#A34B4B]" />
                <span>{submitError}</span>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-[#D8D5CD]">
              <button
                type="button"
                onClick={closeReviewModal}
                disabled={isSubmitting}
                className="px-3 py-1.5 rounded text-[12px] font-semibold text-[#5F6360] hover:bg-[#F5F3EE] transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDecisionSubmit}
                disabled={isSubmitting || operatorNotes.trim().length < 10}
                className={`px-3.5 py-1.5 rounded text-[12px] font-semibold transition-colors flex items-center gap-1 cursor-pointer ${
                  modalState.action === "CONFIRM"
                    ? "bg-[#1F5A43] text-white hover:bg-[#184835] disabled:bg-[#D8D5CD] disabled:cursor-not-allowed"
                    : "bg-[#A34B4B] text-white hover:bg-[#853838] disabled:bg-[#D8D5CD] disabled:cursor-not-allowed"
                }`}
              >
                {isSubmitting ? (
                  <span>Submitting...</span>
                ) : modalState.action === "CONFIRM" ? (
                  "Confirm Root Cause"
                ) : (
                  "Reject Hypothesis"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
