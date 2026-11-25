
"use client";

import React, { use, useEffect, useState, useCallback, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  fetchIncidentById,
  fetchIncidentExplanation,
  fetchIncidentRecommendations,
  generateIncidentRecommendations,
  investigateIncident,
  fetchIncidentReports,
  generateIncidentReport,
  IncidentDetailResponse,
  IncidentExplanationResponse,
  IncidentInvestigationResponse,
  RecommendationListResponse,
  ReportListResponse,
} from "@/lib/api/incidents";
import { ApiError } from "@/lib/api/logs";
import { IncidentHeader } from "@/components/incidents/IncidentHeader";
import { OverviewRcaTab } from "@/components/incidents/tabs/OverviewRcaTab";
import { TimelineEvidenceTab } from "@/components/incidents/tabs/TimelineEvidenceTab";
import { AiInvestigationTab } from "@/components/incidents/tabs/AiInvestigationTab";
import { RecommendationsTab } from "@/components/incidents/tabs/RecommendationsTab";
import { PostMortemReportsTab } from "@/components/incidents/tabs/PostMortemReportsTab";
import { IncidentTabNav, IncidentTabId } from "@/components/incidents/IncidentTabNav";

interface PageProps {
  params: Promise<{ id: string }>;
}

function getImpactLabel(impact: string): string {
  switch (impact?.toUpperCase()) {
    case "PRIMARY_CULPRIT":
      return "INITIAL OBSERVED SERVICE";
    case "DOWNSTREAM_CASCADE":
      return "DOWNSTREAM CASCADE";
    case "CORRELATED_ANOMALY":
      return "CORRELATED ANOMALY";
    default:
      return impact || "AFFECTED";
  }
}

function getImpactVariant(impact: string): StatusVariant {
  switch (impact?.toUpperCase()) {
    case "PRIMARY_CULPRIT":
      return "info";
    case "DOWNSTREAM_CASCADE":
      return "warning";
    case "CORRELATED_ANOMALY":
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

function IncidentDetailContent({ params }: PageProps) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const tabParam = searchParams?.get("tab");

  // Cross-render refs for request synchronization and stale-guard protection
  const currentIncidentIdRef = useRef<string>(id);
  useEffect(() => {
    currentIncidentIdRef.current = id;
  }, [id]);

  const explanationRequestIdRef = useRef<number>(0);

  const [incident, setIncident] = useState<IncidentDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isNotFound, setIsNotFound] = useState<boolean>(false);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const [activeTab, setActiveTab] = useState<IncidentTabId>(() => {
    if (tabParam) {
      const normalized = tabParam.toLowerCase();
      if (normalized === "investigate" || normalized === "investigation") return "investigate";
      if (normalized === "reports" || normalized === "report" || normalized === "post-mortem") return "reports";
      if (normalized === "timeline" || normalized === "evidence") return "timeline";
      if (normalized === "recommendations" || normalized === "recommendation") return "recommendations";
      if (normalized === "overview" || normalized === "rca" || normalized === "hypothesis") return "overview";
    }
    return "overview";
  });

  // Adjust active tab during render when tabParam changes (React 19 pattern)
  const [prevTabParam, setPrevTabParam] = useState(tabParam);
  if (tabParam !== prevTabParam) {
    setPrevTabParam(tabParam);
    if (tabParam) {
      const normalized = tabParam.toLowerCase();
      if (normalized === "investigate" || normalized === "investigation") {
        setActiveTab("investigate");
      } else if (normalized === "reports" || normalized === "report" || normalized === "post-mortem") {
        setActiveTab("reports");
      } else if (normalized === "timeline" || normalized === "evidence") {
        setActiveTab("timeline");
      } else if (normalized === "recommendations" || normalized === "recommendation") {
        setActiveTab("recommendations");
      } else if (normalized === "overview" || normalized === "rca" || normalized === "hypothesis") {
        setActiveTab("overview");
      }
    }
  }

  // AI Diagnostic Explanation State
  const [explanation, setExplanation] = useState<IncidentExplanationResponse | null>(null);
  const [isExplanationLoading, setIsExplanationLoading] = useState<boolean>(false);
  const [isRequestingLlm, setIsRequestingLlm] = useState<boolean>(false);
  const [explanationError, setExplanationError] = useState<string | null>(null);
  const [isLlmRequested, setIsLlmRequested] = useState<boolean>(false);

  // Recommendations State
  const [recommendations, setRecommendations] = useState<RecommendationListResponse | null>(null);
  const [isRecommendationsLoading, setIsRecommendationsLoading] = useState<boolean>(false);
  const [isGeneratingRecommendations, setIsGeneratingRecommendations] = useState<boolean>(false);
  const [recommendationsError, setRecommendationsError] = useState<string | null>(null);

  const recommendationRequestIdRef = useRef<number>(0);

  // Natural-Language Investigation Q&A State
  const [investigationQuery, setInvestigationQuery] = useState<string>("");
  const [investigationResult, setInvestigationResult] = useState<IncidentInvestigationResponse | null>(null);
  const [isSubmittingInvestigation, setIsSubmittingInvestigation] = useState<boolean>(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [investigationValidationMsg, setInvestigationValidationMsg] = useState<string | null>(null);

  const investigationRequestIdRef = useRef<number>(0);

  // Post-Mortem Reports State
  const [reports, setReports] = useState<ReportListResponse | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [isReportsLoading, setIsReportsLoading] = useState<boolean>(false);
  const [isGeneratingReport, setIsGeneratingReport] = useState<boolean>(false);
  const [reportsError, setReportsError] = useState<string | null>(null);

  const reportRequestIdRef = useRef<number>(0);

  const isTemplateView = id === "template";

  const loadIncident = useCallback(() => {
    setRefreshTrigger((prev) => prev + 1);
  }, []);

  const handleLoadExplanation = useCallback(
    async (useLlm: boolean) => {
      if (isTemplateView) return;
      if (isExplanationLoading || isRequestingLlm) return;

      const targetId = currentIncidentIdRef.current;
      const requestId = ++explanationRequestIdRef.current;

      if (useLlm) {
        setIsRequestingLlm(true);
      } else {
        setIsExplanationLoading(true);
      }
      setExplanationError(null);

      try {
        const data = await fetchIncidentExplanation(targetId, { use_llm: useLlm });
        // Stale-response guard: verify incident has not changed and request is latest
        if (
          currentIncidentIdRef.current !== targetId ||
          explanationRequestIdRef.current !== requestId
        ) {
          return;
        }
        setExplanation(data);
        setIsLlmRequested(useLlm);
      } catch (err) {
        if (
          currentIncidentIdRef.current !== targetId ||
          explanationRequestIdRef.current !== requestId
        ) {
          return;
        }
        if (err instanceof ApiError) {
          setExplanationError(err.message);
        } else {
          setExplanationError("Failed to load incident explanation.");
        }
      } finally {
        if (
          currentIncidentIdRef.current === targetId &&
          explanationRequestIdRef.current === requestId
        ) {
          setIsExplanationLoading(false);
          setIsRequestingLlm(false);
        }
      }
    },
    [isTemplateView, isExplanationLoading, isRequestingLlm]
  );

  const handleFetchRecommendations = useCallback(async () => {
    if (isTemplateView) return;
    if (isRecommendationsLoading || isGeneratingRecommendations) return;

    const targetId = currentIncidentIdRef.current;
    const requestId = ++recommendationRequestIdRef.current;

    setIsRecommendationsLoading(true);
    setRecommendationsError(null);

    try {
      const data = await fetchIncidentRecommendations(targetId);
      if (
        currentIncidentIdRef.current !== targetId ||
        recommendationRequestIdRef.current !== requestId
      ) {
        return;
      }
      setRecommendations(data);
    } catch (err) {
      if (
        currentIncidentIdRef.current !== targetId ||
        recommendationRequestIdRef.current !== requestId
      ) {
        return;
      }
      if (err instanceof ApiError) {
        setRecommendationsError(err.message);
      } else {
        setRecommendationsError("Failed to load recommendations.");
      }
    } finally {
      if (
        currentIncidentIdRef.current === targetId &&
        recommendationRequestIdRef.current === requestId
      ) {
        setIsRecommendationsLoading(false);
      }
    }
  }, [isTemplateView, isRecommendationsLoading, isGeneratingRecommendations]);

  const handleGenerateRecommendations = useCallback(
    async (forceRegenerate: boolean = false) => {
      if (isTemplateView) return;
      if (isRecommendationsLoading || isGeneratingRecommendations) return;

      const targetId = currentIncidentIdRef.current;
      const requestId = ++recommendationRequestIdRef.current;

      setIsGeneratingRecommendations(true);
      setRecommendationsError(null);

      try {
        const data = await generateIncidentRecommendations(targetId, {
          force_regenerate: forceRegenerate,
        });
        if (
          currentIncidentIdRef.current !== targetId ||
          recommendationRequestIdRef.current !== requestId
        ) {
          return;
        }
        setRecommendations(data);
      } catch (err) {
        if (
          currentIncidentIdRef.current !== targetId ||
          recommendationRequestIdRef.current !== requestId
        ) {
          return;
        }
        if (err instanceof ApiError) {
          setRecommendationsError(err.message);
        } else {
          setRecommendationsError("Failed to generate troubleshooting recommendations.");
        }
      } finally {
        if (
          currentIncidentIdRef.current === targetId &&
          recommendationRequestIdRef.current === requestId
        ) {
          setIsGeneratingRecommendations(false);
          setIsRecommendationsLoading(false);
        }
      }
    },
    [isTemplateView, isRecommendationsLoading, isGeneratingRecommendations]
  );

  const handleInvestigate = useCallback(
    async (overrideQuery?: string) => {
      if (isTemplateView) return;
      if (isSubmittingInvestigation) return;

      const q = typeof overrideQuery === "string" ? overrideQuery : investigationQuery;
      const clean = q.trim();

      if (clean.length < 3) {
        setInvestigationValidationMsg("Investigation inquiry must be at least 3 characters long.");
        return;
      }
      if (clean.length > 1000) {
        setInvestigationValidationMsg("Investigation inquiry cannot exceed 1000 characters.");
        return;
      }

      setInvestigationValidationMsg(null);
      setInvestigationError(null);
      setIsSubmittingInvestigation(true);

      const targetId = currentIncidentIdRef.current;
      const requestId = ++investigationRequestIdRef.current;

      try {
        const data = await investigateIncident(targetId, clean);
        if (
          currentIncidentIdRef.current !== targetId ||
          investigationRequestIdRef.current !== requestId
        ) {
          return;
        }
        setInvestigationResult(data);
      } catch (err) {
        if (
          currentIncidentIdRef.current !== targetId ||
          investigationRequestIdRef.current !== requestId
        ) {
          return;
        }
        if (err instanceof ApiError) {
          setInvestigationError(err.message);
        } else {
          setInvestigationError("Failed to execute incident investigation inquiry.");
        }
      } finally {
        if (
          currentIncidentIdRef.current === targetId &&
          investigationRequestIdRef.current === requestId
        ) {
          setIsSubmittingInvestigation(false);
        }
      }
    },
    [isTemplateView, isSubmittingInvestigation, investigationQuery]
  );

  const handleClearInvestigation = useCallback(() => {
    setInvestigationQuery("");
    setInvestigationResult(null);
    setInvestigationError(null);
    setInvestigationValidationMsg(null);
  }, []);

  const handleFetchReports = useCallback(async () => {
    if (isTemplateView) return;
    if (isReportsLoading || isGeneratingReport) return;

    const targetId = currentIncidentIdRef.current;
    const requestId = ++reportRequestIdRef.current;

    setIsReportsLoading(true);
    setReportsError(null);

    try {
      const data = await fetchIncidentReports(targetId);
      if (
        currentIncidentIdRef.current !== targetId ||
        reportRequestIdRef.current !== requestId
      ) {
        return;
      }
      setReports(data);
      if (data.items.length > 0) {
        setSelectedReportId((prev) => {
          if (prev && data.items.some((r) => r.id === prev)) {
            return prev;
          }
          return data.items[0].id;
        });
      } else {
        setSelectedReportId(null);
      }
    } catch (err) {
      if (
        currentIncidentIdRef.current !== targetId ||
        reportRequestIdRef.current !== requestId
      ) {
        return;
      }
      if (err instanceof ApiError) {
        setReportsError(err.message);
      } else {
        setReportsError("Failed to load post-mortem reports.");
      }
    } finally {
      if (
        currentIncidentIdRef.current === targetId &&
        reportRequestIdRef.current === requestId
      ) {
        setIsReportsLoading(false);
      }
    }
  }, [isTemplateView, isReportsLoading, isGeneratingReport]);

  const handleGenerateReport = useCallback(
    async (customTitle?: string) => {
      if (isTemplateView) return;
      if (isReportsLoading || isGeneratingReport) return;

      const targetId = currentIncidentIdRef.current;
      const requestId = ++reportRequestIdRef.current;

      setIsGeneratingReport(true);
      setReportsError(null);

      try {
        const newReport = await generateIncidentReport(
          targetId,
          customTitle ? { title: customTitle } : undefined
        );
        if (
          currentIncidentIdRef.current !== targetId ||
          reportRequestIdRef.current !== requestId
        ) {
          return;
        }

        const updatedList = await fetchIncidentReports(targetId);
        if (
          currentIncidentIdRef.current !== targetId ||
          reportRequestIdRef.current !== requestId
        ) {
          return;
        }
        setReports(updatedList);
        setSelectedReportId(newReport.id);
      } catch (err) {
        if (
          currentIncidentIdRef.current !== targetId ||
          reportRequestIdRef.current !== requestId
        ) {
          return;
        }
        if (err instanceof ApiError) {
          setReportsError(err.message);
        } else {
          setReportsError("Failed to generate post-mortem report.");
        }
      } finally {
        if (
          currentIncidentIdRef.current === targetId &&
          reportRequestIdRef.current === requestId
        ) {
          setIsGeneratingReport(false);
          setIsReportsLoading(false);
        }
      }
    },
    [isTemplateView, isReportsLoading, isGeneratingReport]
  );

  useEffect(() => {
    let cancelled = false;

    async function execute() {
      await Promise.resolve();
      if (cancelled) return;

      if (isTemplateView) {
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setError(null);
      setIsNotFound(false);
      setExplanation(null);
      setExplanationError(null);
      setIsLlmRequested(false);
      setIsExplanationLoading(true);
      setRecommendations(null);
      setRecommendationsError(null);
      setIsRecommendationsLoading(true);

      // Reset investigation state on incident change
      setInvestigationQuery("");
      setInvestigationResult(null);
      setInvestigationError(null);
      setInvestigationValidationMsg(null);
      setIsSubmittingInvestigation(false);

      // Reset reports state on incident change
      setReports(null);
      setSelectedReportId(null);
      setReportsError(null);
      setIsReportsLoading(true);
      setIsGeneratingReport(false);

      const targetId = id;
      const explanationRequestId = ++explanationRequestIdRef.current;
      const recRequestId = ++recommendationRequestIdRef.current;
      const repRequestId = ++reportRequestIdRef.current;

      try {
        const data = await fetchIncidentById(targetId);
        if (cancelled || currentIncidentIdRef.current !== targetId) return;
        setIncident(data);

        // Fetch deterministic explanation by default
        try {
          const expData = await fetchIncidentExplanation(targetId, { use_llm: false });
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            explanationRequestIdRef.current === explanationRequestId
          ) {
            setExplanation(expData);
          }
        } catch (expErr) {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            explanationRequestIdRef.current === explanationRequestId
          ) {
            setExplanationError(
              expErr instanceof ApiError
                ? expErr.message
                : "Failed to load diagnostic explanation."
            );
          }
        } finally {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            explanationRequestIdRef.current === explanationRequestId
          ) {
            setIsExplanationLoading(false);
          }
        }

        // Fetch recommendations by default
        try {
          const recData = await fetchIncidentRecommendations(targetId);
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            recommendationRequestIdRef.current === recRequestId
          ) {
            setRecommendations(recData);
          }
        } catch (recErr) {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            recommendationRequestIdRef.current === recRequestId
          ) {
            setRecommendationsError(
              recErr instanceof ApiError
                ? recErr.message
                : "Failed to load recommendations."
            );
          }
        } finally {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            recommendationRequestIdRef.current === recRequestId
          ) {
            setIsRecommendationsLoading(false);
          }
        }
        // Fetch post-mortem reports by default
        try {
          const repData = await fetchIncidentReports(targetId);
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            reportRequestIdRef.current === repRequestId
          ) {
            setReports(repData);
            if (repData.items.length > 0) {
              setSelectedReportId(repData.items[0].id);
            }
          }
        } catch (repErr) {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            reportRequestIdRef.current === repRequestId
          ) {
            setReportsError(
              repErr instanceof ApiError
                ? repErr.message
                : "Failed to load post-mortem reports."
            );
          }
        } finally {
          if (
            !cancelled &&
            currentIncidentIdRef.current === targetId &&
            reportRequestIdRef.current === repRequestId
          ) {
            setIsReportsLoading(false);
          }
        }
      } catch (err) {
        if (cancelled || currentIncidentIdRef.current !== targetId) return;
        if (err instanceof ApiError) {
          if (err.status === 404) {
            setIsNotFound(true);
          } else {
            setError(err.message);
          }
        } else {
          setError("Failed to load incident from backend.");
        }
      } finally {
        if (!cancelled && currentIncidentIdRef.current === targetId) {
          setIsLoading(false);
          if (explanationRequestIdRef.current === explanationRequestId) {
            setIsExplanationLoading(false);
          }
          if (recommendationRequestIdRef.current === recRequestId) {
            setIsRecommendationsLoading(false);
          }
          if (reportRequestIdRef.current === repRequestId) {
            setIsReportsLoading(false);
          }
        }
      }
    }

    execute();

    return () => {
      cancelled = true;
      explanationRequestIdRef.current += 1;
      recommendationRequestIdRef.current += 1;
      investigationRequestIdRef.current += 1;
      reportRequestIdRef.current += 1;
    };
  }, [id, isTemplateView, refreshTrigger]);

  // Template layout view
  if (isTemplateView) {
    return (
      <div className="flex flex-col w-full bg-[#F5F3EE] min-h-screen text-[#171A18]">
        <section className="bg-white border-b border-[#D8D5CD] px-4 sm:px-6 py-4">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div className="flex flex-col gap-1.5 min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-[12px]">
                <Link href="/incidents" className="text-[#5F6360] hover:text-[#171A18]">
                  Incidents
                </Link>
                <span className="text-[#5F6360]">/</span>
                <span className="font-mono text-[#5F6360]">template</span>
                <StatusBadge label="ARCHITECTURAL TEMPLATE" variant="neutral" />
              </div>
              <h1 className="text-[22px] font-semibold text-[#171A18]">
                Incident Layout Architecture Template
              </h1>
            </div>
            <Link
              href="/incidents"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#D8D5CD] text-[#5F6360] text-[13px] hover:bg-[#EAE7DF] transition-colors"
            >
              <Icon name="arrow_back" size={16} />
              <span>Back to Incidents</span>
            </Link>
          </div>
        </section>
        <div className="p-6 text-[13px] text-[#5F6360]">
          This is the static architectural template reference. Navigate to <Link href="/incidents" className="text-[#1F5A43] hover:underline">Incidents</Link> to inspect live persisted incidents.
        </div>
      </div>
    );
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex flex-col w-full bg-[#F5F3EE] min-h-screen p-6 gap-6">
        <div className="h-8 w-64 bg-[#EAE7DF] rounded-sm animate-pulse" />
        <div className="h-24 w-full bg-[#EAE7DF] rounded-xl animate-pulse" />
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-7 h-96 bg-[#EAE7DF] rounded-xl animate-pulse" />
          <div className="lg:col-span-5 h-96 bg-[#EAE7DF] rounded-xl animate-pulse" />
        </div>
      </div>
    );
  }

  // Not Found State
  if (isNotFound) {
    return (
      <div className="flex flex-col items-center justify-center p-12 gap-4 text-center max-w-lg mx-auto my-12 bg-white border border-[#D8D5CD] rounded-xl shadow-xs">
        <div className="w-14 h-14 rounded-2xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-center text-[#A34B4B]">
          <Icon name="search_off" size={28} />
        </div>
        <h2 className="text-[18px] font-semibold text-[#171A18]">Incident Not Found</h2>
        <p className="text-[13px] text-[#5F6360] leading-relaxed">
          The requested incident ID <code className="font-mono text-[#171A18] bg-[#F5F3EE] px-1.5 py-0.5 rounded border border-[#D8D5CD]">{id}</code> does not exist in the PostgreSQL incident catalogue.
        </p>
        <Link
          href="/incidents"
          className="mt-2 flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#1F5A43] text-white text-[13px] font-medium hover:bg-[#184634] transition-colors cursor-pointer"
        >
          <Icon name="arrow_back" size={16} />
          <span>Return to Incidents Catalogue</span>
        </Link>
      </div>
    );
  }

  // Error State
  if (error || !incident) {
    return (
      <div className="p-6 flex flex-col items-center justify-center gap-4 text-center max-w-md mx-auto my-12 bg-white border border-[#D8D5CD] rounded-xl shadow-xs">
        <div className="w-12 h-12 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-center justify-center text-[#A34B4B]">
          <Icon name="error" size={24} />
        </div>
        <h2 className="text-[16px] font-semibold text-[#171A18]">Failed to Load Incident</h2>
        <p className="text-[13px] text-[#5F6360]">{error || "Unknown error occurred"}</p>
        <button
          type="button"
          onClick={loadIncident}
          className="px-4 py-2 rounded-lg bg-[#1F5A43] text-white text-[13px] font-medium hover:bg-[#184634] transition-colors cursor-pointer"
        >
          Retry Connection
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col w-full bg-[#F5F3EE] min-h-screen text-[#171A18]">
      {/* Incident Header & Lifecycle Actions */}
      <section className="px-4 sm:px-6 pt-4 pb-2">
        <IncidentHeader
          incident={incident}
          onIncidentUpdated={setIncident}
          onRefresh={loadIncident}
          isRefreshing={isLoading}
        />
      </section>

      {/* Incident Workspace Tab Navigation */}
      <IncidentTabNav
        activeTab={activeTab}
        onTabChange={setActiveTab}
        timelineEventsCount={incident.timeline_events.length}
        evidenceItemsCount={incident.evidence_items.length}
        recommendationsCount={recommendations?.total ?? undefined}
        reportsCount={reports?.total ?? undefined}
        disabledTabs={[]}
      />

      {/* Incident Layout Body */}
      <section className="p-4 sm:p-6 flex flex-col gap-5">
        {/* Truthful RCA & Uncertainty Banner */}
        <div className="p-4 rounded-xl bg-white border border-[#D8D5CD] flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-[#171A18] shadow-xs">
          <div className="flex items-start sm:items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-center text-[#1F5A43] shrink-0">
              <Icon name="info" size={20} />
            </div>
            <div className="flex flex-col">
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-semibold text-[#171A18]">
                  Earliest Observed Failure (Algorithmically Identified)
                </span>
                <StatusBadge label="CONFIRMED CAUSALITY: FALSE" variant="neutral" />
              </div>
              <p className="text-[12px] text-[#5F6360] mt-0.5 leading-relaxed">
                {incident.uncertainty_reason ||
                  "Earliest observed failure was identified chronologically. Root-cause confirmation requires operator review or automated graph causality verification."}
              </p>
            </div>
          </div>
          <StatusBadge label="PERSISTED INCIDENT" variant="neutral" />
        </div>

        {/* Overview & RCA Dedicated Tab View */}
        {activeTab === "overview" && (
          <div
            role="tabpanel"
            id="panel-overview"
            aria-labelledby="tab-overview"
            className="w-full flex flex-col gap-5"
          >
            {/* Affected Blast Radius Services */}
            <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
              <div className="flex items-center justify-between border-b border-[#D8D5CD] pb-3">
                <div className="flex items-center gap-2">
                  <Icon name="hub" size={18} className="text-[#1F5A43]" />
                  <h3 className="text-[14px] font-semibold text-[#171A18]">
                    Affected Blast Radius ({incident.affected_services.length} Services)
                  </h3>
                </div>
                <StatusBadge
                  label={incident.affected_services.length > 1 ? "MULTI-SERVICE" : "SINGLE-SERVICE"}
                  variant="neutral"
                />
              </div>

              <div className="flex flex-col gap-2.5">
                {incident.affected_services.map((svc) => (
                  <div
                    key={svc.service_name}
                    className="p-3 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-between"
                  >
                    <div className="flex flex-col gap-0.5">
                      <span className="font-mono text-[13px] font-semibold text-[#171A18]">
                        {svc.service_name}
                      </span>
                      <span className="text-[11px] text-[#5F6360]">
                        First impacted: {formatUtc(svc.first_impacted_at)}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[12px] text-[#5F6360]">
                        {svc.error_count} error(s)
                      </span>
                      <StatusBadge
                        label={getImpactLabel(svc.impact_level)}
                        variant={getImpactVariant(svc.impact_level)}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Overview & RCA Tab Component */}
            <OverviewRcaTab
              incident={incident}
              explanation={explanation}
              isExplanationLoading={isExplanationLoading}
              isRequestingLlm={isRequestingLlm}
              explanationError={explanationError}
              isLlmRequested={isLlmRequested}
              onLoadExplanation={handleLoadExplanation}
            />
          </div>
        )}

        {/* Timeline & Evidence Dedicated Tab View */}
        {activeTab === "timeline" && (
          <div
            role="tabpanel"
            id="panel-timeline"
            aria-labelledby="tab-timeline"
            className="w-full flex flex-col gap-5"
          >
            <TimelineEvidenceTab
              timelineEvents={incident.timeline_events}
              evidenceItems={incident.evidence_items}
              incident={incident}
            />
          </div>
        )}

        {/* AI Investigation Dedicated Tab View */}
        {activeTab === "investigate" && (
          <div
            role="tabpanel"
            id="panel-investigate"
            aria-labelledby="tab-investigate"
            className="w-full flex flex-col gap-5"
          >
            <AiInvestigationTab
              incident={incident}
              investigationQuery={investigationQuery}
              setInvestigationQuery={setInvestigationQuery}
              investigationResult={investigationResult}
              isSubmittingInvestigation={isSubmittingInvestigation}
              investigationError={investigationError}
              investigationValidationMsg={investigationValidationMsg}
              setInvestigationValidationMsg={setInvestigationValidationMsg}
              onInvestigate={handleInvestigate}
              onClearInvestigation={handleClearInvestigation}
            />
          </div>
        )}

        {/* Recommendations Dedicated Tab View */}
        {activeTab === "recommendations" && (
          <div
            role="tabpanel"
            id="panel-recommendations"
            aria-labelledby="tab-recommendations"
            className="w-full flex flex-col gap-5"
          >
            <RecommendationsTab
              incident={incident}
              recommendations={recommendations}
              isRecommendationsLoading={isRecommendationsLoading}
              isGeneratingRecommendations={isGeneratingRecommendations}
              recommendationsError={recommendationsError}
              onFetchRecommendations={handleFetchRecommendations}
              onGenerateRecommendations={handleGenerateRecommendations}
            />
          </div>
        )}

        {/* Post-Mortem Reports Dedicated Tab View */}
        {activeTab === "reports" && (
          <div
            role="tabpanel"
            id="panel-reports"
            aria-labelledby="tab-reports"
            className="w-full flex flex-col gap-5"
          >
            <PostMortemReportsTab
              incident={incident}
              reports={reports}
              selectedReportId={selectedReportId}
              onSelectReportId={setSelectedReportId}
              isReportsLoading={isReportsLoading}
              isGeneratingReport={isGeneratingReport}
              reportsError={reportsError}
              onFetchReports={handleFetchReports}
              onGenerateReport={handleGenerateReport}
            />
          </div>
        )}
      </section>
    </div>
  );
}

export default function IncidentDetailPage({ params }: PageProps) {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col w-full bg-[#F5F3EE] min-h-screen p-6 gap-6">
          <div className="h-8 w-64 bg-[#EAE7DF] rounded-sm animate-pulse" />
          <div className="h-24 w-full bg-[#EAE7DF] rounded-xl animate-pulse" />
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-7 h-96 bg-[#EAE7DF] rounded-xl animate-pulse" />
            <div className="lg:col-span-5 h-96 bg-[#EAE7DF] rounded-xl animate-pulse" />
          </div>
        </div>
      }
    >
      <IncidentDetailContent params={params} />
    </Suspense>
  );
}
