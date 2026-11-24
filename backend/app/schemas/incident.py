from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class IncidentCandidateRequest(BaseModel):
    """Request payload to query and evaluate incident candidates from indexed logs."""

    model_config = ConfigDict(from_attributes=True)

    start_time: Optional[datetime] = Field(
        None, description="Start timestamp filter boundary in UTC (ISO-8601)"
    )
    end_time: Optional[datetime] = Field(
        None, description="End timestamp filter boundary in UTC (ISO-8601)"
    )
    service: Optional[str] = Field(
        None, description="Optional service name filter"
    )
    environment: Optional[str] = Field(
        None, description="Optional deployment environment filter"
    )
    window_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Bounded time window in minutes for clustering log message patterns",
    )
    cascade_window_seconds: int = Field(
        default=120,
        ge=1,
        le=3600,
        description="Propagation threshold in seconds for heuristic temporal cascade correlation",
    )
    max_logs: int = Field(
        default=1000,
        ge=1,
        le=5000,
        description="Maximum error and fatal log records to retrieve for candidate evaluation",
    )
    include_sequence_anomalies: bool = Field(
        default=False,
        description="Whether to include LogBERT sequence anomaly evaluation",
    )


class CorrelatedTimelineEventResponse(BaseModel):
    """Chronological timeline milestone in a candidate incident."""

    model_config = ConfigDict(from_attributes=True)

    sequence_order: int = Field(..., description="Zero-based chronological sequence order")
    event_timestamp: datetime = Field(..., description="Timestamp when event occurred (UTC)")
    service_name: str = Field(..., description="Originating service identifier")
    event_type: str = Field(
        ...,
        description="Classification of milestone ('FIRST_ERROR', 'CASCADING_FAILURE', 'ANOMALY_SPIKE')",
    )
    severity: str = Field(..., description="Canonical log level or event severity")
    summary: str = Field(..., description="Concise human-readable summary of this event")
    is_earliest_observed_failure: bool = Field(
        ...,
        description="Whether this represents the earliest observed error/fatal failure in this candidate",
    )
    propagation_lag_seconds: float = Field(
        ...,
        description="Delay in seconds relative to the earliest observed failure event",
    )
    cluster_id: str = Field(..., description="Originating log pattern cluster identifier")
    trace_id: Optional[str] = Field(default=None, description="Distributed tracing trace ID if available")
    span_id: Optional[str] = Field(default=None, description="Distributed tracing span ID if available")
    sample_log_id: Optional[str] = Field(default=None, description="Source OpenSearch document ID")
    relationship_type: Optional[str] = Field(
        default=None,
        description="Classified relationship type ('TRACE_BASED', 'SPAN_BASED', 'REQUEST_BASED', 'SERVICE_LOCAL', 'TEMPORAL_ASSOCIATION', 'SUSPECTED_DEPENDENCY')",
    )
    correlation_confidence: Optional[float] = Field(
        default=None,
        description="Traceable correlation confidence score between 0.0 and 1.0",
    )
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Metadata and diagnostic attributes"
    )


class CorrelatedEvidenceItemResponse(BaseModel):
    """Verifiable evidence pointer supporting a candidate correlation."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str = Field(
        ..., description="Evidence classification ('CULPRIT_LOG', 'CORRELATION_SPIKE')"
    )
    log_id: str = Field(..., description="Exact source OpenSearch document ID")
    timestamp: datetime = Field(..., description="Log event timestamp in UTC")
    service_name: str = Field(..., description="Service emitting the log")
    log_level: str = Field(..., description="Severity level ('FATAL', 'ERROR', etc.)")
    snippet: str = Field(..., description="Bounded unmanipulated excerpt of the log message")
    correlation_rule: str = Field(
        ...,
        description="Rule used to link this evidence ('FIRST_FAILURE', 'TRACE_LINKED', 'SPAN_LINKED', 'TEMPORAL_CASCADE', 'LOCAL_SERVICE')",
    )
    rationale: str = Field(..., description="Explicit rationale for linking this log to the incident")
    content_hash: Optional[str] = Field(
        default=None, description="SHA-256 hash of raw log for tamper-evident provenance"
    )
    trace_id: Optional[str] = Field(default=None, description="Distributed tracing trace ID if present")
    relationship_type: Optional[str] = Field(
        default=None,
        description="Classified relationship type ('TRACE_BASED', 'SPAN_BASED', 'REQUEST_BASED', 'SERVICE_LOCAL', 'TEMPORAL_ASSOCIATION')",
    )
    correlation_confidence: Optional[float] = Field(
        default=None,
        description="Traceable correlation confidence score between 0.0 and 1.0",
    )


class CorrelatedAffectedServiceResponse(BaseModel):
    """Service affected by this candidate incident and its observed failure role."""

    model_config = ConfigDict(from_attributes=True)

    service_name: str = Field(..., description="Service identifier")
    impact_level: str = Field(
        ...,
        description="Role in failure ('PRIMARY_CULPRIT', 'DOWNSTREAM_CASCADE', 'CORRELATED_ANOMALY')",
    )
    first_impacted_at: datetime = Field(
        ..., description="Timestamp of first observed error for this service"
    )
    error_count: int = Field(..., description="Count of error/fatal logs in candidate window")


class EvidenceReferenceResponse(BaseModel):
    """Immutable, deterministic reference to source evidence preserving exact provenance."""

    model_config = ConfigDict(from_attributes=True)

    log_id: str = Field(..., description="Source log record identifier")
    service_name: str = Field(..., description="Service emitting the log record")
    timestamp: Optional[datetime] = Field(None, description="Timestamp of source log in UTC")
    evidence_type: str = Field(..., description="Evidence category ('CULPRIT_LOG', 'CORRELATION_SPIKE', etc.)")
    correlation_rule: str = Field(..., description="Correlation rule applied")
    trace_id: Optional[str] = Field(None, description="Distributed trace identifier if present")
    span_id: Optional[str] = Field(None, description="Distributed span identifier if present")
    content_hash: Optional[str] = Field(None, description="SHA-256 provenance hash if present")
    log_snippet: Optional[str] = Field(None, description="Exact or safe supporting log text snippet")

    @classmethod
    def from_reference(cls, ref: Any) -> "EvidenceReferenceResponse":
        """Instantiate EvidenceReferenceResponse from an EvidenceReference."""
        return cls(
            log_id=str(getattr(ref, "log_id", "") or ""),
            service_name=str(getattr(ref, "service_name", "") or "unknown"),
            timestamp=getattr(ref, "timestamp", None),
            evidence_type=str(getattr(ref, "evidence_type", "") or "EVIDENCE"),
            correlation_rule=str(getattr(ref, "correlation_rule", "") or "UNKNOWN"),
            trace_id=getattr(ref, "trace_id", None),
            span_id=getattr(ref, "span_id", None),
            content_hash=getattr(ref, "content_hash", None),
            log_snippet=getattr(ref, "log_snippet", None),
        )


class RCACitationItemResponse(BaseModel):
    """Structured citation pointing to verified supporting evidence for an RCA claim."""

    model_config = ConfigDict(from_attributes=True)

    log_id: str = Field(..., description="Source log record identifier")
    service_name: str = Field(..., description="Service emitting the log record")
    evidence_type: str = Field(
        ...,
        description="Evidence category ('CULPRIT_LOG', 'CORRELATION_SPIKE', 'TIMELINE_EVENT', etc.)",
    )
    timestamp: Optional[datetime] = Field(None, description="Timestamp of source log in UTC")
    evidence_id: Optional[str] = Field(None, description="OpenSearch document ID or incident evidence UUID")
    log_snippet: Optional[str] = Field(None, description="Exact or safe supporting log text snippet")
    support_explanation: str = Field(..., description="Explanation of why this evidence supports the claim")
    trace_id: Optional[str] = Field(None, description="Distributed trace identifier if present")
    span_id: Optional[str] = Field(None, description="Distributed span identifier if present")
    content_hash: Optional[str] = Field(None, description="SHA-256 provenance hash if present")

    @classmethod
    def from_citation(cls, cit: Any) -> "RCACitationItemResponse":
        """Instantiate RCACitationItemResponse from an RCACitationItem."""
        return cls(
            log_id=str(
                getattr(cit, "log_id", "")
                or (cit.get("log_id") if isinstance(cit, dict) else "")
                or getattr(cit, "evidence_id", "")
                or (cit.get("evidence_id") if isinstance(cit, dict) else "")
                or ""
            ),
            service_name=str(
                getattr(cit, "service_name", "")
                or (cit.get("service_name") if isinstance(cit, dict) else "")
                or "unknown"
            ),
            evidence_type=str(
                getattr(cit, "evidence_type", "")
                or (cit.get("evidence_type") if isinstance(cit, dict) else "")
                or "EVIDENCE"
            ),
            timestamp=getattr(cit, "timestamp", None) if not isinstance(cit, dict) else cit.get("timestamp"),
            evidence_id=getattr(cit, "evidence_id", None) if not isinstance(cit, dict) else cit.get("evidence_id"),
            log_snippet=getattr(cit, "log_snippet", None) if not isinstance(cit, dict) else cit.get("log_snippet"),
            support_explanation=str(
                getattr(cit, "support_explanation", "")
                or (cit.get("support_explanation") if isinstance(cit, dict) else "")
                or ""
            ),
            trace_id=getattr(cit, "trace_id", None) if not isinstance(cit, dict) else cit.get("trace_id"),
            span_id=getattr(cit, "span_id", None) if not isinstance(cit, dict) else cit.get("span_id"),
            content_hash=getattr(cit, "content_hash", None) if not isinstance(cit, dict) else cit.get("content_hash"),
        )


class RCAClaimMappingResponse(BaseModel):
    """Structured RCA claim with fine-grained evidence citation mapping."""

    model_config = ConfigDict(from_attributes=True)

    claim_id: str = Field(..., description="Deterministic claim identifier (e.g. 'claim-1')")
    claim_text: str = Field(..., description="The factual claim or hypothesis text")
    claim_type: str = Field(
        ...,
        description="Claim category ('OBSERVED_FACT', 'INFERRED_HYPOTHESIS', 'UNSUPPORTED_SPECULATION')",
    )
    is_supported: bool = Field(..., description="Whether the claim is grounded in verified telemetry")
    citations: List[RCACitationItemResponse] = Field(
        default_factory=list, description="Verified supporting evidence citations"
    )
    unsupported_reason: Optional[str] = Field(
        None, description="Reason if telemetry is insufficient to support the claim"
    )

    @classmethod
    def from_claim(cls, claim: Any) -> "RCAClaimMappingResponse":
        """Instantiate RCAClaimMappingResponse from an RCAClaimMapping."""
        cits_raw = getattr(claim, "citations", []) if not isinstance(claim, dict) else claim.get("citations", [])
        return cls(
            claim_id=str(
                getattr(claim, "claim_id", "")
                or (claim.get("claim_id") if isinstance(claim, dict) else "")
                or ""
            ),
            claim_text=str(
                getattr(claim, "claim_text", "")
                or (claim.get("claim_text") if isinstance(claim, dict) else "")
                or ""
            ),
            claim_type=str(
                getattr(claim, "claim_type", "")
                or (claim.get("claim_type") if isinstance(claim, dict) else "")
                or "OBSERVED_FACT"
            ),
            is_supported=bool(
                getattr(claim, "is_supported", False)
                if not isinstance(claim, dict)
                else claim.get("is_supported", False)
            ),
            citations=[RCACitationItemResponse.from_citation(c) for c in (cits_raw or [])],
            unsupported_reason=getattr(claim, "unsupported_reason", None)
            if not isinstance(claim, dict)
            else claim.get("unsupported_reason"),
        )


class RCACandidateFindingResponse(BaseModel):
    """Structured evidence-backed RCA candidate explanation finding."""

    model_config = ConfigDict(from_attributes=True)

    explanation: str = Field(..., description="Evidence-grounded hypothesis or explanation statement")
    plausibility_rationale: str = Field(..., description="Reasoning for why the hypothesis is plausible")
    supporting_evidence_references: List[EvidenceReferenceResponse] = Field(
        default_factory=list, description="Preserved references to supporting telemetry evidence"
    )
    contradictory_evidence_references: List[EvidenceReferenceResponse] = Field(
        default_factory=list, description="Preserved references to contradictory telemetry evidence"
    )
    contradictory_signals: List[str] = Field(
        default_factory=list, description="Observed contradictory signals or timeline violations"
    )
    missing_telemetry: List[str] = Field(
        default_factory=list, description="Missing signals or unverified telemetry necessary for confirmation"
    )
    limitations_and_uncertainty: str = Field(
        ..., description="Explicit disclosure of diagnostic bounds and limitations"
    )
    is_root_cause_confirmed: bool = Field(
        default=False, description="Strictly False: correlation does not establish causation"
    )
    claim_citations: List[RCAClaimMappingResponse] = Field(
        default_factory=list, description="Structured claim-level evidence citation mappings"
    )
    potential_root_cause_service: Optional[str] = Field(
        None, description="Suspected initiating service candidate (strictly unconfirmed)"
    )
    suspected_failure_mechanism: Optional[str] = Field(
        None, description="Suspected technical failure mechanism or propagation pattern"
    )
    initial_observed_failure: Optional[Dict[str, Any]] = Field(
        None, description="Chronologically earliest observed failure event details"
    )
    downstream_symptoms: List[str] = Field(
        default_factory=list, description="Downstream services or symptoms resulting from propagation"
    )
    alternative_explanations: List[str] = Field(
        default_factory=list, description="Alternative plausible hypotheses when evidence is not conclusive"
    )

    @classmethod
    def from_finding(cls, finding: Any) -> "RCACandidateFindingResponse":
        """Instantiate RCACandidateFindingResponse from an RCACandidateFinding."""
        return cls(
            explanation=str(getattr(finding, "explanation", "") or ""),
            plausibility_rationale=str(getattr(finding, "plausibility_rationale", "") or ""),
            supporting_evidence_references=[
                EvidenceReferenceResponse.from_reference(r)
                for r in (getattr(finding, "supporting_evidence_references", []) or [])
            ],
            contradictory_evidence_references=[
                EvidenceReferenceResponse.from_reference(r)
                for r in (getattr(finding, "contradictory_evidence_references", []) or [])
            ],
            contradictory_signals=list(getattr(finding, "contradictory_signals", []) or []),
            missing_telemetry=list(getattr(finding, "missing_telemetry", []) or []),
            limitations_and_uncertainty=str(getattr(finding, "limitations_and_uncertainty", "") or ""),
            is_root_cause_confirmed=bool(getattr(finding, "is_root_cause_confirmed", False)),
            claim_citations=[
                RCAClaimMappingResponse.from_claim(c)
                for c in (getattr(finding, "claim_citations", []) or [])
            ],
            potential_root_cause_service=getattr(finding, "potential_root_cause_service", None),
            suspected_failure_mechanism=getattr(finding, "suspected_failure_mechanism", None),
            initial_observed_failure=getattr(finding, "initial_observed_failure", None),
            downstream_symptoms=list(getattr(finding, "downstream_symptoms", []) or []),
            alternative_explanations=list(getattr(finding, "alternative_explanations", []) or []),
        )


class RCASynthesisResponse(BaseModel):
    """Structured report containing synthesized RCA candidate findings."""

    model_config = ConfigDict(from_attributes=True)

    classification: str = Field(
        ..., description="Sufficiency classification ('TRACE_LINKED', 'TEMPORAL_ONLY', 'INSUFFICIENT')"
    )
    is_root_cause_confirmed: bool = Field(
        default=False, description="Strictly False: correlation does not establish causation"
    )
    primary_hypothesis: Optional[str] = Field(
        None, description="Primary candidate hypothesis statement (strictly None if INSUFFICIENT)"
    )
    findings: List[RCACandidateFindingResponse] = Field(
        default_factory=list, description="List of evaluated RCA candidate findings"
    )
    observed_facts: List[str] = Field(
        default_factory=list, description="Grounded factual observations"
    )
    uncertainty_statement: str = Field(
        ..., description="Explicit statement of diagnostic uncertainty"
    )
    summary: str = Field(..., description="High-level synthesis summary")
    claim_citations: List[RCAClaimMappingResponse] = Field(
        default_factory=list, description="Structured claim-level evidence citation mappings across the synthesis"
    )
    potential_root_cause_service: Optional[str] = Field(
        None, description="Suspected initiating service candidate (strictly unconfirmed)"
    )
    suspected_failure_mechanism: Optional[str] = Field(
        None, description="Suspected technical failure mechanism or propagation pattern"
    )
    initial_observed_failure: Optional[Dict[str, Any]] = Field(
        None, description="Chronologically earliest observed failure event details"
    )
    downstream_symptoms: List[str] = Field(
        default_factory=list, description="Downstream services or symptoms resulting from propagation"
    )
    alternative_explanations: List[str] = Field(
        default_factory=list, description="Alternative plausible hypotheses when evidence is not conclusive"
    )

    @classmethod
    def from_result(cls, result: Any) -> "RCASynthesisResponse":
        """Instantiate RCASynthesisResponse from an RCASynthesisResult."""
        cls_val = (
            result.classification.value
            if hasattr(result.classification, "value")
            else str(result.classification)
        )
        return cls(
            classification=cls_val,
            is_root_cause_confirmed=bool(getattr(result, "is_root_cause_confirmed", False)),
            primary_hypothesis=getattr(result, "primary_hypothesis", None),
            findings=[
                RCACandidateFindingResponse.from_finding(f)
                for f in (getattr(result, "findings", []) or [])
            ],
            observed_facts=list(getattr(result, "observed_facts", []) or []),
            uncertainty_statement=str(getattr(result, "uncertainty_statement", "") or ""),
            summary=str(getattr(result, "summary", "") or ""),
            claim_citations=[
                RCAClaimMappingResponse.from_claim(c)
                for c in (getattr(result, "claim_citations", []) or [])
            ],
            potential_root_cause_service=getattr(result, "potential_root_cause_service", None),
            suspected_failure_mechanism=getattr(result, "suspected_failure_mechanism", None),
            initial_observed_failure=getattr(result, "initial_observed_failure", None),
            downstream_symptoms=list(getattr(result, "downstream_symptoms", []) or []),
            alternative_explanations=list(getattr(result, "alternative_explanations", []) or []),
        )


class EvidenceAssessmentResponse(BaseModel):
    """Structured deterministic assessment of incident evidence sufficiency and diagnostic bounds."""

    model_config = ConfigDict(from_attributes=True)

    classification: str = Field(
        ...,
        description="Deterministic sufficiency classification ('TRACE_LINKED', 'TEMPORAL_ONLY', 'INSUFFICIENT')",
    )
    is_root_cause_confirmed: bool = Field(
        default=False,
        description="Explicit confirmation flag; strictly False as correlation does not establish causation",
    )
    distinct_evidence_count: int = Field(
        ..., description="Count of distinct corroborating source evidence records"
    )
    distinct_service_count: int = Field(
        ..., description="Count of distinct services evaluated in the incident"
    )
    has_shared_trace: bool = Field(
        ..., description="Whether relevant shared distributed trace linkage exists across services or events"
    )
    trace_ids_found: List[str] = Field(
        default_factory=list, description="All trace identifiers observed in telemetry"
    )
    span_ids_found: List[str] = Field(
        default_factory=list, description="All span identifiers observed in telemetry"
    )
    is_timeline_temporally_consistent: bool = Field(
        ..., description="Whether milestone event sequence and propagation lags are temporally consistent"
    )
    observed_facts: List[str] = Field(
        default_factory=list, description="Objective, signal-grounded statements of observed telemetry facts"
    )
    inferred_hypotheses: List[str] = Field(
        default_factory=list, description="Explicitly labeled non-confirmed hypotheses"
    )
    supporting_signals: List[str] = Field(
        default_factory=list, description="Corroborating telemetry signals detected"
    )
    missing_signals: List[str] = Field(
        default_factory=list, description="Missing or unverified telemetry signals required for definitive diagnosis"
    )
    uncertainty_statement: str = Field(
        ..., description="Explicit statement of diagnostic uncertainty and bounds"
    )
    rca_synthesis: Optional[RCASynthesisResponse] = Field(
        None, description="Synthesized evidence-backed RCA candidate findings"
    )

    @classmethod
    def from_assessment(cls, assessment: Any, rca_result: Optional[Any] = None) -> "EvidenceAssessmentResponse":
        """Instantiate EvidenceAssessmentResponse from an EvidenceSufficiencyAssessment."""
        cls_val = (
            assessment.classification.value
            if hasattr(assessment.classification, "value")
            else str(assessment.classification)
        )
        resolved_rca = rca_result or getattr(assessment, "rca_synthesis", None)
        rca_resp = (
            RCASynthesisResponse.from_result(resolved_rca)
            if resolved_rca is not None
            else None
        )
        return cls(
            classification=cls_val,
            is_root_cause_confirmed=bool(getattr(assessment, "is_root_cause_confirmed", False)),
            distinct_evidence_count=int(getattr(assessment, "distinct_evidence_count", 0)),
            distinct_service_count=int(getattr(assessment, "distinct_service_count", 0)),
            has_shared_trace=bool(getattr(assessment, "has_shared_trace", False)),
            trace_ids_found=list(getattr(assessment, "trace_ids_found", []) or []),
            span_ids_found=list(getattr(assessment, "span_ids_found", []) or []),
            is_timeline_temporally_consistent=bool(
                getattr(assessment, "is_timeline_temporally_consistent", True)
            ),
            observed_facts=list(getattr(assessment, "observed_facts", []) or []),
            inferred_hypotheses=list(getattr(assessment, "inferred_hypotheses", []) or []),
            supporting_signals=list(getattr(assessment, "supporting_signals", []) or []),
            missing_signals=list(getattr(assessment, "missing_signals", []) or []),
            uncertainty_statement=str(getattr(assessment, "uncertainty_statement", "") or ""),
            rca_synthesis=rca_resp,
        )


class IncidentCandidateResponse(BaseModel):
    """A deterministic candidate incident evaluated from available telemetry."""

    model_config = ConfigDict(from_attributes=True)

    candidate_id: str = Field(..., description="Deterministic unique candidate identifier")
    title: str = Field(..., description="Descriptive summary of the incident candidate")
    severity: str = Field(
        ..., description="Assessed severity ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')"
    )
    started_at: datetime = Field(..., description="Timestamp of earliest observed event in UTC")
    ended_at: datetime = Field(..., description="Timestamp of latest observed event in UTC")
    correlation_rules_used: List[str] = Field(
        default_factory=list,
        description="List of deterministic correlation rules applied ('TRACE_LINKED', 'SPAN_LINKED', 'TEMPORAL_CASCADE', 'LOCAL_SERVICE')",
    )
    is_root_cause_confirmed: bool = Field(
        default=False,
        description="Explicit verification flag. Always False at candidate stage; chronological precedence is not proven root cause.",
    )
    uncertainty_reason: str = Field(
        ...,
        description="Truthful explanation of diagnostic uncertainty and bounds of evidence",
    )
    affected_services: List[CorrelatedAffectedServiceResponse] = Field(
        default_factory=list, description="Blast radius services and impact levels"
    )
    timeline_events: List[CorrelatedTimelineEventResponse] = Field(
        default_factory=list, description="Chronologically sorted milestone events"
    )
    evidence_items: List[CorrelatedEvidenceItemResponse] = Field(
        default_factory=list, description="Verifiable evidence pointers linking to source logs"
    )
    cluster_ids: List[str] = Field(
        default_factory=list, description="Associated pattern cluster identifiers"
    )
    assessment: Optional[EvidenceAssessmentResponse] = Field(
        None,
        description="Structured deterministic assessment of evidence sufficiency and bounds",
    )
    rca_synthesis: Optional[RCASynthesisResponse] = Field(
        None,
        description="Synthesized evidence-backed RCA candidate findings",
    )


class IncidentCandidatesResponse(BaseModel):
    """Collection of candidate incidents evaluated from indexed log telemetry."""

    model_config = ConfigDict(from_attributes=True)

    total_candidates: int = Field(..., description="Total count of candidate incidents evaluated")
    total_affected_services: int = Field(
        ..., description="Distinct affected service count across all evaluated candidates"
    )
    candidates: List[IncidentCandidateResponse] = Field(
        default_factory=list, description="List of evaluated incident candidates"
    )


class IncidentCreateRequest(BaseModel):
    """Payload to persist an evaluated candidate incident to PostgreSQL."""

    model_config = ConfigDict(from_attributes=True)

    candidate_id: str = Field(..., description="Deterministic unique candidate identifier")
    title: str = Field(..., description="Descriptive summary of the incident")
    severity: str = Field(..., description="Severity level ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')")
    started_at: datetime = Field(..., description="Timestamp of earliest observed failure in UTC")
    ended_at: Optional[datetime] = Field(None, description="Timestamp of recovery or boundary end in UTC")
    correlation_rules_used: List[str] = Field(
        default_factory=list,
        description="Correlation rules applied during candidate evaluation",
    )
    uncertainty_reason: Optional[str] = Field(
        None,
        description="Truthful explanation of diagnostic uncertainty and bounds of evidence",
    )
    affected_services: List[CorrelatedAffectedServiceResponse] = Field(
        default_factory=list, description="Blast radius services and impact levels"
    )
    timeline_events: List[CorrelatedTimelineEventResponse] = Field(
        default_factory=list, description="Chronological timeline milestone events"
    )
    evidence_items: List[CorrelatedEvidenceItemResponse] = Field(
        default_factory=list, description="Verifiable evidence items linking to source logs"
    )
    cluster_ids: List[str] = Field(
        default_factory=list, description="Associated pattern cluster identifiers"
    )


class IncidentSummaryItemResponse(BaseModel):
    """Summary item schema for incident catalogue listing."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique incident UUID")
    title: str = Field(..., description="Incident title summary")
    severity: str = Field(..., description="Incident severity level")
    status: str = Field(..., description="Operational lifecycle status ('DETECTED', 'INVESTIGATING', etc.)")
    analysis_status: str = Field(..., description="RCA analysis status ('PENDING', 'EVALUATED_UNCERTAIN', etc.)")
    started_at: datetime = Field(..., description="Timestamp of earliest anomaly event in UTC")
    ended_at: Optional[datetime] = Field(None, description="Timestamp of boundary end or recovery in UTC")
    detected_at: datetime = Field(..., description="Timestamp incident was registered by system in UTC")
    confidence_score: float = Field(..., description="Diagnostic confidence index")
    is_root_cause_confirmed: bool = Field(
        ...,
        description="Explicit verification flag; never set automatically by thresholds alone",
    )
    uncertainty_reason: Optional[str] = Field(None, description="Explicit uncertainty explanation")
    primary_service: Optional[str] = Field(None, description="Primary suspect service name")
    affected_service_count: int = Field(0, description="Count of affected blast radius services")
    event_count: int = Field(0, description="Count of chronological timeline events")
    evidence_count: int = Field(0, description="Count of supporting evidence items")
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record update timestamp")


class IncidentListResponse(BaseModel):
    """Paginated collection of persisted incidents."""

    model_config = ConfigDict(from_attributes=True)

    items: List[IncidentSummaryItemResponse] = Field(
        default_factory=list, description="List of persisted incidents on current page"
    )
    total: int = Field(..., description="Total number of matching incident records")
    page: int = Field(..., description="Current 1-based page number")
    page_size: int = Field(..., description="Page size limit")
    total_pages: int = Field(..., description="Total number of pages")


class IncidentDetailResponse(BaseModel):
    """Full detail view of an individual persisted incident."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique incident UUID")
    title: str = Field(..., description="Incident title summary")
    severity: str = Field(..., description="Incident severity level")
    status: str = Field(..., description="Operational lifecycle status")
    analysis_status: str = Field(..., description="RCA analysis status")
    started_at: datetime = Field(..., description="Timestamp of earliest anomaly event in UTC")
    ended_at: Optional[datetime] = Field(None, description="Timestamp of boundary end in UTC")
    detected_at: datetime = Field(..., description="Timestamp incident was registered in UTC")
    probable_cause: Optional[str] = Field(None, description="Formulated root cause hypothesis")
    confidence_score: float = Field(..., description="Diagnostic confidence index")
    is_root_cause_confirmed: bool = Field(
        ...,
        description="Explicit verification flag; never set automatically by thresholds alone",
    )
    uncertainty_reason: Optional[str] = Field(None, description="Explicit uncertainty explanation")
    primary_service: Optional[str] = Field(None, description="Primary suspect service name")
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record update timestamp")
    affected_services: List[CorrelatedAffectedServiceResponse] = Field(
        default_factory=list, description="Blast radius services and impact levels"
    )
    timeline_events: List[CorrelatedTimelineEventResponse] = Field(
        default_factory=list, description="Chronological timeline milestone events"
    )
    evidence_items: List[CorrelatedEvidenceItemResponse] = Field(
        default_factory=list, description="Verifiable evidence items linking to source logs"
    )
    assessment: Optional[EvidenceAssessmentResponse] = Field(
        None,
        description="Structured deterministic assessment of evidence sufficiency and bounds",
    )
    rca_synthesis: Optional[RCASynthesisResponse] = Field(
        None,
        description="Synthesized evidence-backed RCA candidate findings",
    )


class IncidentStatusEnum(str, Enum):
    """Supported operational lifecycle statuses for RootLens incidents."""

    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class IncidentStatusUpdateRequest(BaseModel):
    """Payload to transition incident operational lifecycle status."""

    model_config = ConfigDict(from_attributes=True)

    status: IncidentStatusEnum = Field(
        ...,
        description="Target operational status ('DETECTED', 'INVESTIGATING', 'MITIGATED', 'RESOLVED', 'FALSE_POSITIVE')",
    )


class HypothesisDecisionEnum(str, Enum):
    """Supported operator decision actions on RCA hypotheses."""

    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


class RCAHypothesisDecisionCreateRequest(BaseModel):
    """Payload for an operator reviewing an RCA hypothesis."""

    model_config = ConfigDict(from_attributes=True)

    decision: HypothesisDecisionEnum = Field(
        ...,
        description="Operator decision: 'CONFIRM' (promote to confirmed root cause) or 'REJECT'",
    )
    notes: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Mandatory operator explanation and rationale (minimum 10 characters)",
    )
    hypothesis_text: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional text of the hypothesis being confirmed or rejected",
    )


class RCAHypothesisActionRequest(BaseModel):
    """Payload for dedicated confirm/reject action endpoints with mandatory notes."""

    model_config = ConfigDict(from_attributes=True)

    notes: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Mandatory operator explanation and rationale (minimum 10 characters)",
    )
    hypothesis_text: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional text of the hypothesis being confirmed or rejected",
    )


class RCAHypothesisDecisionResponse(BaseModel):
    """Immutable audit record of an operator hypothesis decision."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique decision audit record UUID")
    incident_id: str = Field(..., description="Associated incident UUID")
    hypothesis_id: str = Field(..., description="Target hypothesis identifier")
    hypothesis_text: str = Field(..., description="Hypothesis claim text")
    operator_id: str = Field(..., description="Authenticated operator ID")
    operator_name: str = Field(..., description="Operator display name")
    operator_email: str = Field(..., description="Operator email address")
    decision: str = Field(..., description="'CONFIRM' or 'REJECT'")
    notes: str = Field(..., description="Mandatory operator justification")
    evidence_citations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Snapshot of verified supporting citations at decision time",
    )
    previous_decision: Optional[str] = Field(
        None, description="Prior decision status for this hypothesis if any"
    )
    decided_at: datetime = Field(..., description="UTC timestamp of the decision")
    created_at: datetime = Field(..., description="UTC timestamp of record creation")


class RCAHypothesisDecisionsSummaryResponse(BaseModel):
    """Aggregated status and history of operator decisions for an incident."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: str = Field(..., description="Incident UUID")
    is_root_cause_confirmed: bool = Field(
        ..., description="Whether a hypothesis is currently confirmed as the root cause"
    )
    confirmed_root_cause: Optional[str] = Field(
        None, description="Confirmed root cause text if confirmed"
    )
    confirmed_hypothesis_id: Optional[str] = Field(
        None, description="Hypothesis ID that is currently confirmed if any"
    )
    active_decisions: Dict[str, RCAHypothesisDecisionResponse] = Field(
        default_factory=dict,
        description="Latest decision per hypothesis_id",
    )
    history: List[RCAHypothesisDecisionResponse] = Field(
        default_factory=list,
        description="Full immutable audit history ordered newest first",
    )


class IncidentGroupingEvaluationResponse(BaseModel):
    """Evaluation result determining whether an event belongs to an existing incident."""

    model_config = ConfigDict(from_attributes=True)

    matches: bool = Field(..., description="Whether the event correlates to the incident")
    incident_id: str = Field(..., description="Target incident identifier")
    match_rule: str = Field(..., description="Rule or reason for match or rejection")
    confidence: float = Field(..., description="Correlation confidence metric (0.0 to 1.0)")
    rationale: str = Field(..., description="Detailed explanation of the correlation decision")
    is_duplicate_log: bool = Field(False, description="Whether log record was already present in incident")
    relationship_type: Optional[str] = Field(None, description="Classified relationship type")


class CorrelateEventRequest(BaseModel):
    """Request payload to evaluate or append an event to an existing incident."""

    model_config = ConfigDict(from_attributes=True)

    log_id: str = Field(..., description="Source log record identifier")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")
    service_name: Optional[str] = Field("unassigned", description="Originating service identifier")
    message: str = Field(..., description="Log message text")
    log_level: str = Field("ERROR", description="Severity or log level")
    trace_id: Optional[str] = Field(None, description="Distributed trace ID")
    span_id: Optional[str] = Field(None, description="Distributed span ID")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Metadata attributes")


