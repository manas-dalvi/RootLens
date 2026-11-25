"use client";

import React, { useState, useRef } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import {
  IncidentDetailResponse,
  IncidentLifecycleStatus,
  updateIncidentStatus,
} from "@/lib/api/incidents";
import { ApiError } from "@/lib/api/logs";
import { StatusTransitionModal } from "./StatusTransitionModal";

export interface IncidentHeaderProps {
  incident: IncidentDetailResponse;
  onIncidentUpdated: (updated: IncidentDetailResponse) => void;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

function formatUtcTimestamp(dateStr?: string | null): string {
  if (!dateStr) return "Ongoing / None";
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

function getStatusVariant(status?: string): StatusVariant {
  switch (status?.toUpperCase()) {
    case "RESOLVED":
      return "healthy";
    case "INVESTIGATING":
      return "info";
    case "MITIGATED":
      return "warning";
    case "DETECTED":
      return "critical";
    case "FALSE_POSITIVE":
      return "neutral";
    default:
      return "neutral";
  }
}

export function IncidentHeader({
  incident,
  onIncidentUpdated,
  onRefresh,
  isRefreshing = false,
}: IncidentHeaderProps) {
  const [isUpdatingStatus, setIsUpdatingStatus] = useState<boolean>(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [pendingStatus, setPendingStatus] = useState<IncidentLifecycleStatus | null>(null);
  const [modalTitle, setModalTitle] = useState<string>("");
  const [modalMessage, setModalMessage] = useState<string>("");

  const triggerButtonRef = useRef<HTMLButtonElement | null>(null);

  const currentStatus = incident.status as IncidentLifecycleStatus;

  const executeTransition = async (targetStatus: IncidentLifecycleStatus) => {
    setIsUpdatingStatus(true);
    setUpdateError(null);

    try {
      const updated = await updateIncidentStatus(incident.id, targetStatus);
      setIsModalOpen(false);
      setPendingStatus(null);
      onIncidentUpdated(updated);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setUpdateError(err.detail || err.message);
      } else {
        const msg = err instanceof Error ? err.message : "Failed to update incident status";
        setUpdateError(msg);
      }
    } finally {
      setIsUpdatingStatus(false);
    }
  };

  const openConfirmation = (
    targetStatus: IncidentLifecycleStatus,
    title: string,
    message: string,
    triggerEl: HTMLButtonElement | null
  ) => {
    triggerButtonRef.current = triggerEl;
    setPendingStatus(targetStatus);
    setModalTitle(title);
    setModalMessage(message);
    setUpdateError(null);
    setIsModalOpen(true);
  };

  return (
    <div className="flex flex-col gap-4 w-full bg-[#F5F3EE]">
      {/* Top Bar: Navigation link & Refresh button */}
      <div className="flex items-center justify-between pb-2 border-b border-[#D8D5CD]">
        <Link
          href="/incidents"
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[#5F6360] hover:text-[#171A18] transition-colors"
        >
          <Icon name="arrow_back" size={16} />
          <span>Back to Incidents Catalogue</span>
        </Link>

        <div className="flex items-center gap-2">
          <Link
            href="/logs"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#D8D5CD] bg-white hover:bg-[#EAE7DF] text-[#171A18] text-[12px] font-medium transition-colors shadow-xs"
          >
            <Icon name="search" size={15} className="text-[#5F6360]" />
            <span>Explore Logs</span>
          </Link>

          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={isRefreshing || isUpdatingStatus}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#D8D5CD] bg-white hover:bg-[#EAE7DF] text-[#171A18] text-[12px] font-medium transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
            >
              <Icon
                name="refresh"
                size={15}
                className={`text-[#5F6360] ${isRefreshing ? "animate-spin text-[#1F5A43]" : ""}`}
              />
              <span>{isRefreshing ? "Refreshing..." : "Refresh"}</span>
            </button>
          )}
        </div>
      </div>

      {/* Error alert banner if status update fails */}
      {updateError && (
        <div
          role="alert"
          className="p-3.5 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-start justify-between gap-3 text-[#A34B4B]"
        >
          <div className="flex items-start gap-2.5">
            <Icon name="error" size={18} className="shrink-0 mt-0.5" />
            <div className="flex flex-col gap-0.5">
              <span className="font-semibold text-[13px]">Status Transition Rejected</span>
              <span className="text-[12px] leading-relaxed">{updateError}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setUpdateError(null)}
            className="text-[#A34B4B] hover:text-[#8B3F3F] p-1 rounded-md transition-colors"
            aria-label="Dismiss error"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      )}

      {/* Main Header Card */}
      <div className="p-6 rounded-xl bg-white border border-[#D8D5CD] shadow-xs flex flex-col gap-5 text-[#171A18]">
        {/* Title, Severity, Operational Status & Action Controls */}
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="flex flex-col gap-2 min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <StatusBadge
                label={`SEV: ${incident.severity || "UNKNOWN"}`}
                variant={getSeverityVariant(incident.severity)}
              />
              <StatusBadge
                label={`STATUS: ${incident.status || "DETECTED"}`}
                variant={getStatusVariant(incident.status)}
                dot
              />
              {incident.analysis_status && (
                <span className="px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-[#F5F3EE] text-[#5F6360] border border-[#D8D5CD]">
                  TELEMETRY: {incident.analysis_status}
                </span>
              )}
            </div>

            <h1 className="text-[22px] font-semibold text-[#171A18] tracking-tight leading-snug break-words">
              {incident.title}
            </h1>

            <div className="flex items-center gap-2 font-mono text-[11px] text-[#5F6360]">
              <span>ID: {incident.id}</span>
            </div>
          </div>

          {/* Lifecycle Action Buttons */}
          <div className="flex flex-wrap items-center gap-2 shrink-0 pt-2 lg:pt-0">
            {currentStatus === "DETECTED" && (
              <>
                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={() => executeTransition("INVESTIGATING")}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 text-[13px] font-semibold rounded-lg bg-[#1F5A43] hover:bg-[#184634] text-white shadow-xs transition-colors disabled:opacity-50 cursor-pointer"
                >
                  {isUpdatingStatus ? (
                    <Icon name="sync" size={16} className="animate-spin" />
                  ) : (
                    <Icon name="search" size={16} />
                  )}
                  <span>Start Investigation</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "MITIGATED",
                      "Mark Incident as Mitigated",
                      "This records that operational impact has been mitigated and service recovery is underway. Sets the ended_at timestamp if empty.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-[#F5F3EE] hover:bg-[#EAE7DF] text-[#9A7440] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="shield" size={16} />
                  <span>Mark Mitigated</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "RESOLVED",
                      "Resolve Incident",
                      "This marks the incident as permanently resolved and closes active operational triage. Terminal transition.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-[#F5F3EE] hover:bg-[#EAE7DF] text-[#1F5A43] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="check_circle" size={16} />
                  <span>Resolve</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "FALSE_POSITIVE",
                      "Close as False Positive",
                      "This marks the candidate correlation as a non-incident or false alarm. Terminal transition.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-white hover:bg-[#FEECEB] text-[#A34B4B] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="cancel" size={16} />
                  <span>False Positive</span>
                </button>
              </>
            )}

            {currentStatus === "INVESTIGATING" && (
              <>
                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "MITIGATED",
                      "Mark Incident as Mitigated",
                      "This indicates active mitigation steps have reduced impact and recovery is progressing. Sets the ended_at timestamp.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 text-[13px] font-semibold rounded-lg bg-[#9A7440] hover:bg-[#856335] text-white shadow-xs transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="shield" size={16} />
                  <span>Mark Mitigated</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "RESOLVED",
                      "Resolve Incident",
                      "This marks the incident as permanently resolved and concludes investigation. Terminal transition.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-[#F5F3EE] hover:bg-[#EAE7DF] text-[#1F5A43] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="check_circle" size={16} />
                  <span>Resolve Incident</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "FALSE_POSITIVE",
                      "Close as False Positive",
                      "This marks the investigation as closed due to false-alarm telemetry. Terminal transition.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-white hover:bg-[#FEECEB] text-[#A34B4B] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="cancel" size={16} />
                  <span>False Positive</span>
                </button>
              </>
            )}

            {currentStatus === "MITIGATED" && (
              <>
                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "RESOLVED",
                      "Resolve Incident",
                      "All impacted microservices have stabilized and post-incident verification is complete. Sets the final resolution timestamp.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 text-[13px] font-semibold rounded-lg bg-[#1F5A43] hover:bg-[#184634] text-white shadow-xs transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="check_circle" size={16} />
                  <span>Resolve Incident</span>
                </button>

                <button
                  type="button"
                  disabled={isUpdatingStatus}
                  onClick={(e) =>
                    openConfirmation(
                      "INVESTIGATING",
                      "Reopen Incident Investigation",
                      "This reopens active diagnostic triage and clears the ended_at timestamp.",
                      e.currentTarget
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-[#F5F3EE] hover:bg-[#EAE7DF] text-[#9A7440] transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Icon name="replay" size={16} />
                  <span>Reopen Investigation</span>
                </button>
              </>
            )}

            {currentStatus === "RESOLVED" && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#EDF9ED] border border-[#C5ECC4] text-[#1F5A43] text-[12px] font-medium">
                <Icon name="check_circle" size={15} />
                <span>Resolved at {formatUtcTimestamp(incident.ended_at)}</span>
              </span>
            )}

            {currentStatus === "FALSE_POSITIVE" && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[#5F6360] text-[12px] font-medium">
                <Icon name="cancel" size={15} />
                <span>Closed as False Positive</span>
              </span>
            )}
          </div>
        </div>

        {/* Telemetry Metadata Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-3 border-t border-[#D8D5CD] text-[12px]">
          {/* Primary Culprit */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[#5F6360] font-medium">Initial Observed Culprit</span>
            <div className="flex items-center gap-1.5 text-[#171A18] font-semibold">
              <Icon name="warning" size={15} className="text-[#9A7440]" />
              <span className="font-mono">{incident.primary_service || "Unidentified"}</span>
            </div>
          </div>

          {/* Blast Radius */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[#5F6360] font-medium">Blast Radius (Services)</span>
            <div className="flex items-center gap-1.5 text-[#171A18] font-semibold">
              <Icon name="hub" size={15} className="text-[#1F5A43]" />
              <span>
                {incident.affected_services?.length || 0} impacted microservice
                {incident.affected_services?.length === 1 ? "" : "s"}
              </span>
            </div>
          </div>

          {/* Started At */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[#5F6360] font-medium">Failure Window Started</span>
            <span className="font-mono text-[#171A18]">{formatUtcTimestamp(incident.started_at)}</span>
          </div>

          {/* Ended / Resolved At */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[#5F6360] font-medium">Resolved / Ended At</span>
            <span className="font-mono text-[#171A18]">
              {incident.ended_at ? formatUtcTimestamp(incident.ended_at) : "Active (Ongoing)"}
            </span>
          </div>
        </div>

        {/* Affected services chip list */}
        {incident.affected_services && incident.affected_services.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-2">
            <span className="text-[11px] font-medium text-[#5F6360] uppercase tracking-wider mr-1">
              Impacted:
            </span>
            {incident.affected_services.map((svc) => (
              <span
                key={svc.service_name}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono border ${
                  svc.impact_level === "PRIMARY_CULPRIT"
                    ? "bg-[#FEF7E6] text-[#9A7440] border-[#FDE8B3] font-semibold"
                    : svc.impact_level === "DOWNSTREAM_CASCADE"
                    ? "bg-[#EFF6FF] text-[#2563EB] border-[#BFDBFE]"
                    : "bg-[#F5F3EE] text-[#5F6360] border-[#D8D5CD]"
                }`}
              >
                <span>{svc.service_name}</span>
                <span className="text-[10px] text-[#5F6360]">({svc.error_count} errs)</span>
              </span>
            ))}
          </div>
        )}

        {/* Strict Uncertainty Grounding Disclaimer Banner */}
        <div className="p-3 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center justify-between gap-3 text-[12px] text-[#5F6360]">
          <div className="flex items-center gap-2 min-w-0">
            <Icon name="verified_user" size={17} className="text-[#1F5A43] shrink-0" />
            <span className="truncate">
              Telemetry Grounding Invariant: Correlation does not prove real-world causality.
            </span>
          </div>
          <span className="font-mono text-[11px] font-semibold text-[#171A18] shrink-0">
            is_root_cause_confirmed: false
          </span>
        </div>
      </div>

      {/* Confirmation Modal */}
      <StatusTransitionModal
        isOpen={isModalOpen}
        targetStatus={pendingStatus}
        title={modalTitle}
        message={modalMessage}
        errorMessage={updateError}
        isSubmitting={isUpdatingStatus}
        onConfirm={() => {
          if (pendingStatus) {
            executeTransition(pendingStatus);
          }
        }}
        onCancel={() => {
          setIsModalOpen(false);
          setPendingStatus(null);
          setUpdateError(null);
        }}
        triggerRef={triggerButtonRef}
      />
    </div>
  );
}
