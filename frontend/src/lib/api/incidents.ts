import { API_ENDPOINTS } from "@/config/api";
import { ApiError } from "@/lib/api/logs";

export type IncidentLifecycleStatus =
  | "DETECTED"
  | "INVESTIGATING"
  | "MITIGATED"
  | "RESOLVED"
  | "FALSE_POSITIVE";

export interface IncidentStatusUpdateRequest {
  status: IncidentLifecycleStatus;
}


export interface CorrelatedAffectedService {
  service_name: string;
  impact_level: string; // "PRIMARY_CULPRIT" | "DOWNSTREAM_CASCADE" | "CORRELATED_ANOMALY"
  first_impacted_at: string;
  error_count: number;
}

export interface CorrelatedTimelineEvent {
  sequence_order: number;
  event_timestamp: string;
  service_name: string;
  event_type: string; // "FIRST_ERROR" | "CASCADING_FAILURE" | "ANOMALY_SPIKE"
  severity: string;
  summary: string;
  is_earliest_observed_failure: boolean;
  propagation_lag_seconds: number;
  cluster_id: string;
  trace_id?: string | null;
  span_id?: string | null;
  sample_log_id?: string | null;
  relationship_type?: string | null;
  correlation_confidence?: number | null;
  attributes: Record<string, unknown>;
}

export interface CorrelatedEvidenceItem {
  evidence_type: string; // "CULPRIT_LOG" | "CORRELATION_SPIKE"
  log_id: string;
  timestamp: string;
  service_name: string;
  log_level: string;
  snippet: string;
  correlation_rule: string;
  rationale: string;
  content_hash?: string | null;
  trace_id?: string | null;
  relationship_type?: string | null;
  correlation_confidence?: number | null;
}

export interface IncidentSummaryItem {
  id: string;
  title: string;
  severity: string;
  status: string;
  analysis_status: string;
  started_at: string;
  ended_at?: string | null;
  detected_at: string;
  confidence_score: number;
  is_root_cause_confirmed: boolean;
  uncertainty_reason?: string | null;
  primary_service?: string | null;
  affected_service_count: number;
  event_count: number;
  evidence_count: number;
  created_at: string;
  updated_at: string;
}

export interface IncidentListResponse {
  items: IncidentSummaryItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface EvidenceReferenceResponse {
  log_id: string;
  service_name: string;
  timestamp?: string | null;
  evidence_type: string;
  correlation_rule: string;
  trace_id?: string | null;
  span_id?: string | null;
  content_hash?: string | null;
  log_snippet?: string | null;
}

export interface RCACitationItem {
  log_id: string;
  service_name: string;
  evidence_type: string;
  timestamp?: string | null;
  evidence_id?: string | null;
  log_snippet?: string | null;
  support_explanation: string;
  trace_id?: string | null;
  span_id?: string | null;
  content_hash?: string | null;
}

export interface RCAClaimMapping {
  claim_id: string;
  claim_text: string;
  claim_type: "OBSERVED_FACT" | "INFERRED_HYPOTHESIS" | "UNSUPPORTED_SPECULATION" | string;
  is_supported: boolean;
  citations: RCACitationItem[];
  unsupported_reason?: string | null;
}

export interface RCACandidateFindingResponse {
  explanation: string;
  plausibility_rationale: string;
  supporting_evidence_references: EvidenceReferenceResponse[];
  contradictory_evidence_references: EvidenceReferenceResponse[];
  contradictory_signals: string[];
  missing_telemetry: string[];
  limitations_and_uncertainty: string;
  is_root_cause_confirmed: boolean;
  claim_citations?: RCAClaimMapping[];
  potential_root_cause_service?: string | null;
  suspected_failure_mechanism?: string | null;
  initial_observed_failure?: Record<string, any> | null;
  downstream_symptoms?: string[];
  alternative_explanations?: string[];
}

export interface RCASynthesisResponse {
  classification: string;
  is_root_cause_confirmed: boolean;
  primary_hypothesis?: string | null;
  findings: RCACandidateFindingResponse[];
  observed_facts: string[];
  uncertainty_statement: string;
  summary: string;
  claim_citations?: RCAClaimMapping[];
  potential_root_cause_service?: string | null;
  suspected_failure_mechanism?: string | null;
  initial_observed_failure?: Record<string, any> | null;
  downstream_symptoms?: string[];
  alternative_explanations?: string[];
}

export interface EvidenceAssessmentResponse {
  classification: string;
  is_root_cause_confirmed: boolean;
  distinct_evidence_count: number;
  distinct_service_count: number;
  has_shared_trace: boolean;
  trace_ids_found: string[];
  span_ids_found: string[];
  is_timeline_temporally_consistent: boolean;
  observed_facts: string[];
  inferred_hypotheses: string[];
  supporting_signals: string[];
  missing_signals: string[];
  uncertainty_statement: string;
  rca_synthesis?: RCASynthesisResponse | null;
}

export interface EvidenceCitation {
  reference_type: string;
  reference_id: string;
  service_name: string;
}

export interface IncidentExplanationResponse {
  incident_id: string;
  classification: string;
  explanation: string;
  is_root_cause_confirmed: boolean;
  evidence_references: EvidenceCitation[];
  missing_telemetry: string[];
  uncertainty_statement: string;
  omitted_timeline_events_count: number;
  omitted_evidence_records_count: number;
  model_used: string;
  provider_used: string;
}

export interface IncidentInvestigationRequest {
  query: string;
}

export interface IncidentInvestigationResponse {
  incident_id: string;
  query: string;
  answer: string;
  classification: string;
  is_root_cause_confirmed: boolean;
  cited_references: EvidenceCitation[];
  unobserved_entities_mentioned: string[];
  is_grounded: boolean;
  model_used: string;
  provider_used: string;
}

export interface RecommendationGenerateRequest {
  force_regenerate?: boolean;
}

export interface RecommendationResponse {
  id: string;
  incident_id: string;
  action_type: string;
  title: string;
  description: string;
  priority: string;
  sequence_order: number;
  verification_step?: string | null;
  caveat?: string | null;
  evidence_citations?: EvidenceCitation[];
  created_at: string;
  updated_at: string;
  is_advisory_only: boolean;
  is_root_cause_confirmed: boolean;
}

export interface RecommendationListResponse {
  items: RecommendationResponse[];
  total: number;
  incident_id: string;
  telemetry_classification: string;
  is_advisory_only: boolean;
  is_root_cause_confirmed: boolean;
}

export interface ReportCreateRequest {
  title?: string | null;
}

export interface ReportResponse {
  id: string;
  incident_id: string;
  title: string;
  status: string;
  executive_summary: string;
  timeline_summary: string;
  root_cause_analysis: string;
  recommendations_summary: string;
  uncertainty_disclaimer?: string | null;
  generated_at: string;
  created_at: string;
  updated_at: string;
  is_root_cause_confirmed: boolean;
  markdown_content: string;
}

export interface ReportListResponse {
  items: ReportResponse[];
  total: number;
  incident_id: string;
}

export interface IncidentDetailResponse {
  id: string;
  title: string;
  severity: string;
  status: string;
  analysis_status: string;
  started_at: string;
  ended_at?: string | null;
  detected_at: string;
  probable_cause?: string | null;
  confidence_score: number;
  is_root_cause_confirmed: boolean;
  uncertainty_reason?: string | null;
  primary_service?: string | null;
  created_at: string;
  updated_at: string;
  affected_services: CorrelatedAffectedService[];
  timeline_events: CorrelatedTimelineEvent[];
  evidence_items: CorrelatedEvidenceItem[];
  assessment?: EvidenceAssessmentResponse | null;
  rca_synthesis?: RCASynthesisResponse | null;
}

export interface IncidentCandidate {
  candidate_id: string;
  title: string;
  severity: string;
  started_at: string;
  ended_at: string;
  correlation_rules_used: string[];
  is_root_cause_confirmed: boolean;
  uncertainty_reason: string;
  affected_services: CorrelatedAffectedService[];
  timeline_events: CorrelatedTimelineEvent[];
  evidence_items: CorrelatedEvidenceItem[];
  cluster_ids: string[];
  assessment?: EvidenceAssessmentResponse | null;
  rca_synthesis?: RCASynthesisResponse | null;
}

export interface IncidentCandidatesResponse {
  total_candidates: number;
  total_affected_services: number;
  candidates: IncidentCandidate[];
}

export interface IncidentListParams {
  status?: string;
  severity?: string;
  service?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  page_size?: number;
}

/**
 * Fetch paginated list of persisted incidents from GET /api/v1/incidents.
 */
export async function fetchIncidents(
  params: IncidentListParams = {},
  signal?: AbortSignal
): Promise<IncidentListResponse> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.severity) query.set("severity", params.severity);
  if (params.service) query.set("service", params.service);
  if (params.start_time) query.set("start_time", params.start_time);
  if (params.end_time) query.set("end_time", params.end_time);
  if (params.page !== undefined) query.set("page", String(params.page));
  if (params.page_size !== undefined) query.set("page_size", String(params.page_size));

  const url = `${API_ENDPOINTS.incidents.base}${query.toString() ? `?${query.toString()}` : ""}`;

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal,
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // use fallback status
      }
      throw new ApiError(res.status, `Failed to fetch incidents: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Fetch full incident detail by ID from GET /api/v1/incidents/{id}.
 */
export async function fetchIncidentById(id: string): Promise<IncidentDetailResponse> {
  const url = API_ENDPOINTS.incidents.byId(id);

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // use fallback status
      }
      throw new ApiError(res.status, `Failed to load incident detail: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Query candidate incidents evaluated in-memory from indexed log telemetry.
 */
export async function fetchIncidentCandidates(
  params: {
    start_time?: string;
    end_time?: string;
    service?: string;
    window_minutes?: number;
    cascade_window_seconds?: number;
    max_logs?: number;
    include_sequence_anomalies?: boolean;
  } = {}
): Promise<IncidentCandidatesResponse> {
  const url = API_ENDPOINTS.incidents.candidates;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        start_time: params.start_time,
        end_time: params.end_time,
        service: params.service,
        window_minutes: params.window_minutes ?? 15,
        cascade_window_seconds: params.cascade_window_seconds ?? 120,
        max_logs: params.max_logs ?? 1000,
        include_sequence_anomalies: params.include_sequence_anomalies ?? true,
      }),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Candidate evaluation failed: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Persist an evaluated incident candidate into PostgreSQL via POST /api/v1/incidents.
 */
export async function persistIncidentCandidate(
  candidate: IncidentCandidate
): Promise<IncidentDetailResponse> {
  const url = API_ENDPOINTS.incidents.base;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(candidate),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to persist incident: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Retrieve synthesized RCA findings for an incident.
 * In RootLens, RCA synthesis is computed deterministically as part of
 * the incident detail analysis (`rca_synthesis` in `IncidentDetailResponse`).
 */
export async function fetchIncidentRCA(id: string): Promise<RCASynthesisResponse | null> {
  const incident = await fetchIncidentById(id);
  return incident.rca_synthesis ?? null;
}

/**
 * Retrieve strictly read-only, evidence-grounded diagnostic explanation for an incident.
 * Deterministic by default via GET /api/v1/incidents/{id}/explanation.
 */
export async function fetchIncidentExplanation(
  id: string,
  options?: { use_llm?: boolean }
): Promise<IncidentExplanationResponse> {
  const query = new URLSearchParams();
  if (options?.use_llm !== undefined) {
    query.set("use_llm", String(options.use_llm));
  }
  const queryString = query.toString();
  const url = `${API_ENDPOINTS.incidents.explanation(id)}${queryString ? `?${queryString}` : ""}`;

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to load incident explanation: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Execute stateless natural-language inquiry about an incident via POST /api/v1/incidents/{id}/investigate.
 */
export async function investigateIncident(
  id: string,
  queryText: string
): Promise<IncidentInvestigationResponse> {
  const url = API_ENDPOINTS.incidents.investigate(id);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query: queryText }),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Investigation query failed: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Retrieve persisted troubleshooting recommendations for an incident via GET /api/v1/incidents/{id}/recommendations.
 */
export async function fetchIncidentRecommendations(
  id: string
): Promise<RecommendationListResponse> {
  const url = API_ENDPOINTS.incidents.recommendations(id);

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to fetch recommendations: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Generate or idempotently retrieve troubleshooting recommendations via POST /api/v1/incidents/{id}/recommendations.
 */
export async function generateIncidentRecommendations(
  id: string,
  options?: RecommendationGenerateRequest
): Promise<RecommendationListResponse> {
  const url = API_ENDPOINTS.incidents.recommendations(id);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: options ? JSON.stringify(options) : undefined,
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to generate recommendations: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Retrieve an individual recommendation by ID via GET /api/v1/incidents/{id}/recommendations/{recommendationId}.
 */
export async function fetchIncidentRecommendationById(
  id: string,
  recommendationId: string
): Promise<RecommendationResponse> {
  const url = API_ENDPOINTS.incidents.recommendationById(id, recommendationId);

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to load recommendation: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Retrieve all persisted post-mortem reports for an incident via GET /api/v1/incidents/{id}/reports.
 */
export async function fetchIncidentReports(id: string): Promise<ReportListResponse> {
  const url = API_ENDPOINTS.incidents.reports(id);

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to fetch incident reports: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Generate, persist, or idempotently retrieve a post-mortem report via POST /api/v1/incidents/{id}/reports.
 */
export async function generateIncidentReport(
  id: string,
  options?: ReportCreateRequest
): Promise<ReportResponse> {
  const url = API_ENDPOINTS.incidents.reports(id);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: options ? JSON.stringify(options) : undefined,
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to generate incident report: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}


/**
 * Retrieve an individual post-mortem report by ID via GET /api/v1/incidents/{id}/reports/{reportId}.
 */
export async function fetchIncidentReportById(
  id: string,
  reportId: string
): Promise<ReportResponse> {
  const url = API_ENDPOINTS.incidents.reportById(id, reportId);

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (errorJson?.detail) detailMsg = errorJson.detail;
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to load incident report: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}



/**
 * Transition incident operational lifecycle status via PATCH /api/v1/incidents/{id}/status.
 * Follows validated backend transition state machine:
 * DETECTED -> [INVESTIGATING, MITIGATED, RESOLVED, FALSE_POSITIVE]
 * INVESTIGATING -> [MITIGATED, RESOLVED, FALSE_POSITIVE]
 * MITIGATED -> [INVESTIGATING, RESOLVED]
 * Returns full updated IncidentDetailResponse.
 */
export async function updateIncidentStatus(
  id: string,
  targetStatus: IncidentLifecycleStatus
): Promise<IncidentDetailResponse> {
  if (!id || !id.trim()) {
    throw new ApiError(400, "Incident ID must be provided.");
  }
  const url = API_ENDPOINTS.incidents.status(id.trim());

  try {
    const res = await fetch(url, {
      method: "PATCH",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ status: targetStatus }),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (typeof errorJson?.detail === "string") {
          detailMsg = errorJson.detail;
        } else if (Array.isArray(errorJson?.detail)) {
          detailMsg = errorJson.detail
            .map((item: { msg?: string }) => item.msg || JSON.stringify(item))
            .join("; ");
        }
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to update incident status: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}

export type HypothesisDecision = "CONFIRM" | "REJECT";

export interface RCAHypothesisDecisionResponse {
  id: string;
  incident_id: string;
  hypothesis_id: string;
  hypothesis_text: string;
  operator_id: string;
  operator_name: string;
  operator_email: string;
  decision: HypothesisDecision;
  notes: string;
  evidence_citations: Array<Record<string, unknown>>;
  previous_decision?: string | null;
  decided_at: string;
  created_at: string;
}

export interface RCAHypothesisDecisionsSummaryResponse {
  incident_id: string;
  is_root_cause_confirmed: boolean;
  confirmed_root_cause?: string | null;
  confirmed_hypothesis_id?: string | null;
  active_decisions: Record<string, RCAHypothesisDecisionResponse>;
  history: RCAHypothesisDecisionResponse[];
}

export interface RCAHypothesisDecisionSubmitRequest {
  decision: HypothesisDecision;
  notes: string;
  hypothesis_text?: string;
}

export interface OperatorHeaders {
  operatorId?: string;
  operatorName?: string;
  operatorEmail?: string;
  operatorRole?: string;
  authToken?: string;
}

function buildOperatorHeaders(options?: OperatorHeaders): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  // Authenticated Bearer token: use provided token or canonical dev-operator-token
  if (options?.authToken) {
    headers["Authorization"] = `Bearer ${options.authToken}`;
  } else {
    headers["Authorization"] = "Bearer dev-operator-token";
  }
  return headers;
}

/**
 * Fetch hypothesis review status and audit history for an incident.
 */
export async function fetchHypothesisDecisions(
  incidentId: string,
  options?: OperatorHeaders
): Promise<RCAHypothesisDecisionsSummaryResponse> {
  if (!incidentId || !incidentId.trim()) {
    throw new ApiError(400, "Incident ID must be provided.");
  }
  const url = API_ENDPOINTS.incidents.hypothesisDecisions(incidentId.trim());

  try {
    const res = await fetch(url, {
      method: "GET",
      headers: buildOperatorHeaders(options),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (typeof errorJson?.detail === "string") {
          detailMsg = errorJson.detail;
        }
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to fetch hypothesis decisions: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}

/**
 * Submit an operator decision (CONFIRM or REJECT) on a hypothesis with mandatory notes.
 */
export async function submitHypothesisDecision(
  incidentId: string,
  hypothesisId: string,
  payload: RCAHypothesisDecisionSubmitRequest,
  options?: OperatorHeaders
): Promise<RCAHypothesisDecisionResponse> {
  if (!incidentId || !incidentId.trim()) {
    throw new ApiError(400, "Incident ID must be provided.");
  }
  if (!hypothesisId || !hypothesisId.trim()) {
    throw new ApiError(400, "Hypothesis ID must be provided.");
  }
  const url = API_ENDPOINTS.incidents.hypothesisDecision(incidentId.trim(), hypothesisId.trim());

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: buildOperatorHeaders(options),
      body: JSON.stringify(payload),
      cache: "no-store",
    });

    if (!res.ok) {
      let detailMsg = `Request failed with status ${res.status}`;
      try {
        const errorJson = await res.json();
        if (typeof errorJson?.detail === "string") {
          detailMsg = errorJson.detail;
        } else if (Array.isArray(errorJson?.detail)) {
          detailMsg = errorJson.detail
            .map((item: { msg?: string }) => item.msg || JSON.stringify(item))
            .join("; ");
        }
      } catch {
        // fallback
      }
      throw new ApiError(res.status, `Failed to submit hypothesis decision: ${detailMsg}`, detailMsg);
    }

    return await res.json();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(
      0,
      "Cannot connect to RootLens API service. Please verify backend is running.",
      err instanceof Error ? err.message : String(err)
    );
  }
}

