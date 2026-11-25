"use client";

import React, { useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge, StatusVariant } from "@/components/ui/StatusBadge";
import type { LogDocument } from "@/lib/api/logs";

interface LogDetailDrawerProps {
  log: LogDocument | null;
  isLoading?: boolean;
  onClose: () => void;
  onFilterByTrace?: (traceId: string) => void;
}

function getLevelBadgeVariant(level?: string | null): StatusVariant {
  if (!level) return "neutral";
  const upper = level.toUpperCase();
  if (upper === "ERROR" || upper === "FATAL" || upper === "CRITICAL") return "critical";
  if (upper === "WARN" || upper === "WARNING") return "warning";
  if (upper === "INFO") return "info";
  if (upper === "DEBUG" || upper === "TRACE") return "neutral";
  return "neutral";
}

export function LogDetailDrawer({
  log,
  isLoading = false,
  onClose,
  onFilterByTrace,
}: LogDetailDrawerProps) {
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  const copyToClipboard = async (text: string, fieldName: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(fieldName);
      setTimeout(() => {
        setCopiedField((curr) => (curr === fieldName ? null : curr));
      }, 2000);
    } catch {
      // Fallback or ignore clipboard errors safely
    }
  };

  if (!log && !isLoading) {
    return (
      <div className="bg-white rounded-xl border border-[#EDEFF3] p-8 flex flex-col items-center justify-center text-center gap-3 text-[#606366] shadow-xs">
        <div className="w-10 h-10 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] flex items-center justify-center text-[#9B9FA3]">
          <Icon name="troubleshoot" size={20} />
        </div>
        <div className="flex flex-col gap-1">
          <p className="text-[14px] font-semibold text-[#1B1A1A]">No Log Selected</p>
          <p className="text-[12px] text-[#9B9FA3] max-w-xs">
            Select a log row from the table to inspect attributes, correlation traces, and raw payloads.
          </p>
        </div>
      </div>
    );
  }

  if (isLoading && !log) {
    return (
      <div className="bg-white rounded-xl border border-[#EDEFF3] p-8 flex flex-col items-center justify-center text-center gap-3 text-[#606366] shadow-xs">
        <div className="w-8 h-8 border-2 border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
        <p className="text-[13px] font-medium text-[#606366]">Loading log details...</p>
      </div>
    );
  }

  if (!log) return null;

  // Check if there is a real correlated incident identifier in structured attributes
  const correlatedIncidentId =
    typeof log.attributes?.incident_id === "string"
      ? log.attributes.incident_id
      : typeof log.attributes?.incident === "string"
      ? log.attributes.incident
      : null;

  return (
    <div
      className={`bg-white rounded-xl border border-[#EDEFF3] shadow-xs p-5 flex flex-col gap-4 transition-all duration-200 ${
        isExpanded ? "col-span-full" : ""
      }`}
    >
      {/* Drawer Header */}
      <div className="flex items-center justify-between pb-3 border-b border-[#EDEFF3]">
        <div className="flex items-center gap-2.5 flex-wrap">
          <div className="w-7 h-7 rounded-lg bg-[#EEF3FF] border border-[#BFDBFE] flex items-center justify-center text-[#4D80FC]">
            <Icon name="troubleshoot" size={16} />
          </div>
          <h2 className="text-[15px] font-semibold text-[#1B1A1A]">Log Detail</h2>

          {correlatedIncidentId ? (
            <span className="px-2 py-0.5 rounded text-[11px] bg-[#FEF7E6] text-[#B77900] border border-[#FDE8B3] font-semibold font-mono">
              Correlated with {correlatedIncidentId}
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded text-[10px] bg-[#F5F6F9] text-[#9B9FA3] border border-[#EDEFF3] font-medium">
              No incident linked
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1.5 rounded-lg text-[#9B9FA3] hover:text-[#1B1A1A] hover:bg-[#F5F6F9] transition-colors"
            title={isExpanded ? "Collapse view" : "Expand view"}
            aria-label={isExpanded ? "Collapse view" : "Expand view"}
          >
            <Icon name={isExpanded ? "close_fullscreen" : "open_in_full"} size={16} />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#9B9FA3] hover:text-[#1B1A1A] hover:bg-[#F5F6F9] transition-colors"
            title="Close drawer"
            aria-label="Close drawer"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      </div>

      {/* Core Fields Grid */}
      <div className="grid grid-cols-2 gap-3.5 bg-[#F5F6F9] border border-[#EDEFF3] p-4 rounded-lg text-[12px]">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Document ID
          </span>
          <div className="flex items-center gap-1.5 font-mono text-[#1B1A1A]">
            <span className="font-semibold truncate max-w-[150px]" title={log.id}>
              {log.id}
            </span>
            <button
              type="button"
              onClick={() => copyToClipboard(log.id, "id")}
              className="text-[#9B9FA3] hover:text-[#4D80FC] transition-colors p-0.5"
              title="Copy ID"
            >
              <Icon name={copiedField === "id" ? "check" : "content_copy"} size={13} />
            </button>
            {copiedField === "id" && (
              <span className="text-[10px] text-[#2E7D32] font-sans font-medium">Copied!</span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Timestamp (UTC)
          </span>
          <span className="font-mono text-[#1B1A1A] truncate" title={log.timestamp || "None"}>
            {log.timestamp ? new Date(log.timestamp).toISOString() : "—"}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Service
          </span>
          <span className="font-mono font-medium text-[#4D80FC] truncate">
            {log.service_name || "—"}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Level
          </span>
          <div>
            <StatusBadge
              label={log.log_level || "UNKNOWN"}
              variant={getLevelBadgeVariant(log.log_level)}
            />
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Host / Node
          </span>
          <span className="font-mono text-[#606366] truncate" title={log.host || "—"}>
            {log.host || "—"}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            OpenSearch Index
          </span>
          <span className="font-mono text-[#606366] truncate" title={log.index || "—"}>
            {log.index || "—"}
          </span>
        </div>
      </div>

      {/* Message Body */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Log Message
          </span>
          <button
            type="button"
            onClick={() => copyToClipboard(log.message || "", "message")}
            className="text-[11px] font-medium text-[#4D80FC] hover:underline flex items-center gap-1"
          >
            <Icon name={copiedField === "message" ? "check" : "content_copy"} size={13} />
            <span>{copiedField === "message" ? "Copied!" : "Copy"}</span>
          </button>
        </div>
        <div className="bg-[#F5F6F9] border border-[#EDEFF3] p-3 rounded-lg font-mono text-[12px] text-[#1B1A1A] leading-relaxed break-words whitespace-pre-wrap max-h-48 overflow-y-auto">
          {log.message || "—"}
        </div>
      </div>

      {/* Distributed Tracing Details */}
      {(log.trace_id || log.span_id) && (
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
            Distributed Tracing
          </span>
          <div className="bg-[#F5F6F9] border border-[#EDEFF3] rounded-lg overflow-hidden font-mono text-[12px]">
            {log.trace_id && (
              <div className="grid grid-cols-12 px-3 py-2 bg-[#FAFBFD] border-b border-[#EDEFF3] items-center">
                <span className="col-span-4 text-[#9B9FA3] text-[11px]">trace_id</span>
                <div className="col-span-8 flex items-center justify-between gap-1 text-[#4D80FC]">
                  <span className="truncate" title={log.trace_id}>
                    {log.trace_id}
                  </span>
                  {onFilterByTrace && (
                    <button
                      type="button"
                      onClick={() => onFilterByTrace(log.trace_id!)}
                      className="text-[#9B9FA3] hover:text-[#4D80FC] transition-colors p-1"
                      title="Filter search by this trace ID"
                    >
                      <Icon name="search" size={14} />
                    </button>
                  )}
                </div>
              </div>
            )}
            {log.span_id && (
              <div className="grid grid-cols-12 px-3 py-2 items-center">
                <span className="col-span-4 text-[#9B9FA3] text-[11px]">span_id</span>
                <span className="col-span-8 text-[#1B1A1A] truncate" title={log.span_id}>
                  {log.span_id}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Structured Attributes & Metadata */}
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
          Structured Attributes ({Object.keys(log.attributes || {}).length})
        </span>
        {Object.keys(log.attributes || {}).length > 0 ? (
          <div className="bg-[#F5F6F9] border border-[#EDEFF3] rounded-lg overflow-hidden font-mono text-[11px] divide-y divide-[#EDEFF3] max-h-56 overflow-y-auto">
            {Object.entries(log.attributes).map(([key, value]) => (
              <div key={key} className="grid grid-cols-12 px-3 py-1.5 hover:bg-white transition-colors">
                <span className="col-span-5 text-[#606366] font-medium truncate" title={key}>
                  {key}
                </span>
                <span
                  className="col-span-7 text-[#1B1A1A] break-all"
                  title={typeof value === "object" ? JSON.stringify(value) : String(value)}
                >
                  {typeof value === "object" ? JSON.stringify(value) : String(value)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-3 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-[12px] text-[#9B9FA3] italic">
            No structured attributes attached to this log entry.
          </div>
        )}
      </div>

      {/* Raw Log Payload */}
      {log.raw_log && log.raw_log !== log.message && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
              Raw Record
            </span>
            <button
              type="button"
              onClick={() => copyToClipboard(log.raw_log || "", "raw_log")}
              className="text-[11px] font-medium text-[#4D80FC] hover:underline flex items-center gap-1"
            >
              <Icon name={copiedField === "raw_log" ? "check" : "content_copy"} size={13} />
              <span>{copiedField === "raw_log" ? "Copied!" : "Copy"}</span>
            </button>
          </div>
          <div className="bg-[#F5F6F9] border border-[#EDEFF3] p-3 rounded-lg font-mono text-[11px] text-[#606366] leading-relaxed break-all max-h-36 overflow-y-auto">
            {log.raw_log}
          </div>
        </div>
      )}

      {/* Action Footer (Clearly labels unintegrated phases) */}
      <div className="grid grid-cols-2 gap-2.5 pt-2 mt-auto border-t border-[#EDEFF3]">
        <button
          type="button"
          disabled={!log.trace_id}
          onClick={() => log.trace_id && onFilterByTrace && onFilterByTrace(log.trace_id)}
          title={log.trace_id ? "Filter logs by trace_id" : "No trace_id on this log record"}
          className={`w-full px-3 py-2 rounded-lg text-[12px] font-medium border flex items-center justify-center gap-1.5 transition-colors ${
            log.trace_id
              ? "text-[#1B1A1A] bg-white border-[#EDEFF3] hover:bg-[#F5F6F9] cursor-pointer"
              : "text-[#9B9FA3] bg-[#F5F6F9] border-[#EDEFF3] cursor-not-allowed"
          }`}
        >
          <Icon name="sync_alt" size={15} />
          <span>Filter Trace</span>
        </button>

        <Link
          href={`/ai-investigation${log?.service_name ? `?service=${encodeURIComponent(log.service_name)}` : ""}`}
          title="Open AI Root Cause Investigation workspace"
          className="w-full px-3 py-2 rounded-lg text-[12px] font-medium text-[#4D80FC] bg-[#EEF3FF] hover:bg-[#E0EAFF] border border-[#BFDBFE] flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
        >
          <Icon name="psychology" size={15} />
          <span>AI Investigation Workspace</span>
        </Link>
      </div>
    </div>
  );
}
