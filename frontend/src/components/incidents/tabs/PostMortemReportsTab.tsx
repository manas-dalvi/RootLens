"use client";

import React, { useState, useCallback, useRef, useEffect } from "react";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type {
  IncidentDetailResponse,
  ReportListResponse,
} from "@/lib/api/incidents";

export interface PostMortemReportsTabProps {
  incident: IncidentDetailResponse;
  reports: ReportListResponse | null;
  selectedReportId: string | null;
  onSelectReportId: (id: string) => void;
  isReportsLoading: boolean;
  isGeneratingReport: boolean;
  reportsError: string | null;
  onFetchReports: () => Promise<void> | void;
  onGenerateReport: (customTitle?: string) => Promise<void> | void;
  className?: string;
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

export function PostMortemReportsTab({
  incident,
  reports,
  selectedReportId,
  onSelectReportId,
  isReportsLoading,
  isGeneratingReport,
  reportsError,
  onFetchReports,
  onGenerateReport,
  className = "",
}: PostMortemReportsTabProps) {
  const [reportViewMode, setReportViewMode] = useState<"sections" | "markdown">("sections");
  const [copiedMarkdown, setCopiedMarkdown] = useState<boolean>(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const copyTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Clean up any pending timeout on unmount
  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const handleCopyMarkdown = useCallback(async (content: string) => {
    if (copyTimeoutRef.current) {
      clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = null;
    }

    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      setCopiedMarkdown(false);
      setCopyError("Clipboard API is unavailable in this environment.");
      copyTimeoutRef.current = setTimeout(() => {
        setCopyError(null);
      }, 3000);
      return;
    }

    try {
      await navigator.clipboard.writeText(content);
      setCopyError(null);
      setCopiedMarkdown(true);
      copyTimeoutRef.current = setTimeout(() => {
        setCopiedMarkdown(false);
      }, 2000);
    } catch (err) {
      setCopiedMarkdown(false);
      const msg =
        err instanceof Error ? err.message : "Failed to copy markdown to clipboard.";
      setCopyError(msg);
      copyTimeoutRef.current = setTimeout(() => {
        setCopyError(null);
      }, 3000);
    }
  }, []);

  const handleDownloadMarkdown = useCallback((filename: string, content: string) => {
    if (typeof window === "undefined") return;
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename.replace(/[^a-zA-Z0-9_\-]/g, "_")}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  const selectedReport =
    reports?.items.find((r) => r.id === selectedReportId) ||
    reports?.items[0] ||
    null;

  return (
    <div className={`flex flex-col gap-5 w-full ${className}`}>
      {/* Evidence-Grounded Post-Mortem Reports Panel */}
      <div className="p-5 rounded-xl bg-white border border-[#D8D5CD] flex flex-col gap-4 shadow-xs">
        {/* Header Bar */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#D8D5CD] pb-3">
          <div className="flex items-center gap-2">
            <Icon name="article" size={20} className="text-[#1F5A43]" />
            <div className="flex items-center gap-2">
              <h3 className="text-[14px] font-semibold text-[#171A18]">
                Post-Mortem Reports
              </h3>
              {reports && (
                <StatusBadge
                  label={`${reports.total} REPORT${reports.total === 1 ? "" : "S"}`}
                  variant="neutral"
                />
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {selectedReport && (
              <>
                <StatusBadge
                  label={`STATUS: ${selectedReport.status}`}
                  variant="warning"
                />
                <StatusBadge
                  label="CONFIRMED CAUSALITY: FALSE"
                  variant="neutral"
                />
              </>
            )}
            <StatusBadge label="DIAGNOSTIC ARTIFACT" variant="neutral" />

            {/* Top Action Button */}
            <button
              type="button"
              onClick={() => onGenerateReport()}
              disabled={isReportsLoading || isGeneratingReport}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
              aria-label="Generate or regenerate post-mortem report"
            >
              {isGeneratingReport ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>Generating Report...</span>
                </>
              ) : (
                <>
                  <Icon name="auto_awesome" size={14} />
                  <span>
                    {reports && reports.items.length > 0
                      ? "Regenerate Report"
                      : "Generate Report"}
                  </span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Invariant Banner */}
        <div className="p-3 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex items-center gap-2 text-[12px] text-[#5F6360]">
          <Icon name="info" size={16} className="text-[#1F5A43] shrink-0" />
          <span>
            Post-mortem reports are evidence-grounded diagnostic artifacts compiled under deterministic observation rules. RootLens never confirms causality from correlation (
            <strong className="text-[#171A18]">is_root_cause_confirmed: false</strong>). Zero autonomous remediation is performed.
          </span>
        </div>

        {/* Loading State */}
        {isReportsLoading && (
          <div
            className="p-8 flex flex-col items-center justify-center gap-3 text-center"
            role="status"
            aria-live="polite"
          >
            <div className="w-6 h-6 border-2 border-[#1F5A43] border-t-transparent rounded-full animate-spin" />
            <span className="text-[13px] text-[#5F6360]">Loading post-mortem reports...</span>
          </div>
        )}

        {/* Error State */}
        {!isReportsLoading && reportsError && (
          <div
            className="p-4 rounded-lg bg-[#FDF2F2] border border-[#F5C6C6] flex flex-col gap-2.5"
            role="alert"
          >
            <div className="flex items-center gap-2 text-[#A34B4B] text-[13px] font-medium">
              <Icon name="error" size={16} />
              <span>Failed to load post-mortem report</span>
            </div>
            <p className="text-[12px] text-[#5F6360]">{reportsError}</p>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => onFetchReports()}
                disabled={isReportsLoading || isGeneratingReport}
                className="px-3 py-1.5 rounded-md bg-white border border-[#D8D5CD] text-[#171A18] text-[12px] font-medium hover:bg-[#F5F3EE] transition-colors cursor-pointer"
              >
                Retry Load
              </button>
              <button
                type="button"
                onClick={() => onGenerateReport()}
                disabled={isReportsLoading || isGeneratingReport}
                className="px-3 py-1.5 rounded-md bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] transition-colors cursor-pointer"
              >
                Generate New Report
              </button>
            </div>
          </div>
        )}

        {/* Empty State */}
        {!isReportsLoading &&
          !reportsError &&
          (!reports || reports.items.length === 0) && (
            <div className="p-8 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] flex flex-col items-center justify-center text-center gap-3">
              <div className="w-10 h-10 rounded-full bg-white border border-[#D8D5CD] flex items-center justify-center text-[#5F6360]">
                <Icon name="article" size={20} />
              </div>
              <div className="flex flex-col gap-1 max-w-md">
                <h5 className="text-[14px] font-semibold text-[#171A18]">
                  No Post-Mortem Report Generated
                </h5>
                <p className="text-[12px] text-[#5F6360] leading-relaxed">
                  An evidence-grounded post-mortem report has not been assembled for this incident yet. Generate a report to compile executive summaries, timeline milestones, diagnostic RCA hypotheses, and verification steps.
                </p>
              </div>
              <button
                type="button"
                onClick={() => onGenerateReport()}
                disabled={isReportsLoading || isGeneratingReport}
                className="mt-1 flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#1F5A43] text-white text-[12px] font-medium hover:bg-[#184634] disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer"
              >
                {isGeneratingReport ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    <span>Assembling Post-Mortem Report...</span>
                  </>
                ) : (
                  <>
                    <Icon name="auto_awesome" size={14} />
                    <span>Generate Post-Mortem Report</span>
                  </>
                )}
              </button>
            </div>
          )}

        {/* Populated Report Display */}
        {!isReportsLoading && !reportsError && selectedReport && (
          <div className="flex flex-col gap-4">
            {/* Report Controls & Metadata Toolbar */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 p-3.5 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD]">
              <div className="flex flex-col gap-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-[14px] text-[#171A18] truncate">
                    {selectedReport.title}
                  </span>
                  {reports && reports.items.length > 1 && (
                    <select
                      value={selectedReport.id}
                      onChange={(e) => onSelectReportId(e.target.value)}
                      className="px-2 py-0.5 text-[11px] bg-white border border-[#D8D5CD] rounded font-mono text-[#171A18] focus:outline-none focus:ring-1 focus:ring-[#1F5A43]"
                      aria-label="Select report version"
                    >
                      {reports.items.map((r, idx) => (
                        <option key={r.id} value={r.id}>
                          #{reports.items.length - idx} {r.title} ({formatUtc(r.generated_at)})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#5F6360]">
                  <span>
                    Generated:{" "}
                    <strong className="text-[#171A18] font-mono">
                      {formatUtc(selectedReport.generated_at)}
                    </strong>
                  </span>
                  <span>
                    Report ID:{" "}
                    <strong className="text-[#171A18] font-mono">
                      {selectedReport.id.slice(0, 8)}...
                    </strong>
                  </span>
                  <span>
                    Incident:{" "}
                    <strong className="text-[#171A18] font-mono">
                      {(selectedReport.incident_id || incident.id).slice(0, 8)}...
                    </strong>
                  </span>
                </div>
              </div>

              {/* Toolbar Actions */}
              <div className="flex items-center gap-2 shrink-0 flex-wrap">
                {/* View Mode Toggle */}
                <div className="flex items-center bg-white border border-[#D8D5CD] rounded-lg p-0.5 text-[11px] font-medium">
                  <button
                    type="button"
                    onClick={() => setReportViewMode("sections")}
                    className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                      reportViewMode === "sections"
                        ? "bg-[#1F5A43] text-white"
                        : "text-[#5F6360] hover:text-[#171A18]"
                    }`}
                  >
                    Sections View
                  </button>
                  <button
                    type="button"
                    onClick={() => setReportViewMode("markdown")}
                    className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                      reportViewMode === "markdown"
                        ? "bg-[#1F5A43] text-white"
                        : "text-[#5F6360] hover:text-[#171A18]"
                    }`}
                  >
                    Markdown View
                  </button>
                </div>

                {/* Copy Markdown */}
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => void handleCopyMarkdown(selectedReport.markdown_content)}
                    className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-[11.5px] font-medium transition-colors cursor-pointer ${
                      copyError
                        ? "bg-[#FDF2F2] border-[#F5C6C6] text-[#A34B4B]"
                        : "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F5F3EE]"
                    }`}
                    title={
                      copyError
                        ? `Copy failed: ${copyError}`
                        : "Copy full report as Markdown"
                    }
                    aria-label="Copy markdown content to clipboard"
                  >
                    <Icon
                      name={
                        copiedMarkdown
                          ? "check"
                          : copyError
                          ? "error"
                          : "content_copy"
                      }
                      size={13}
                      className={
                        copiedMarkdown
                          ? "text-[#1F5A43]"
                          : copyError
                          ? "text-[#A34B4B]"
                          : "text-[#5F6360]"
                      }
                    />
                    <span>
                      {copiedMarkdown
                        ? "Copied"
                        : copyError
                        ? "Copy Failed"
                        : "Copy Markdown"}
                    </span>
                  </button>
                  {copyError && (
                    <span className="text-[11px] text-[#A34B4B]" role="alert">
                      {copyError}
                    </span>
                  )}
                </div>

                {/* Download Markdown */}
                <button
                  type="button"
                  onClick={() =>
                    handleDownloadMarkdown(
                      selectedReport.title,
                      selectedReport.markdown_content
                    )
                  }
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white border border-[#D8D5CD] text-[#171A18] text-[11.5px] font-medium hover:bg-[#F5F3EE] transition-colors cursor-pointer"
                  title="Download Markdown report file"
                >
                  <Icon name="download" size={13} className="text-[#5F6360]" />
                  <span>Export .md</span>
                </button>
              </div>
            </div>

            {/* Mode: Structured Sections View */}
            {reportViewMode === "sections" && (
              <div className="flex flex-col gap-4">
                {/* Section 1: Executive Summary */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1.5">
                    <Icon name="summarize" size={14} className="text-[#1F5A43]" />
                    <span>1. Executive Summary</span>
                  </span>
                  <div className="p-4 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap font-sans">
                    {selectedReport.executive_summary}
                  </div>
                </div>

                {/* Section 2: Chronological Timeline */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1.5">
                    <Icon name="timeline" size={14} className="text-[#1F5A43]" />
                    <span>2. Chronological Timeline Narrative</span>
                  </span>
                  <div className="p-4 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap font-sans">
                    {selectedReport.timeline_summary}
                  </div>
                </div>

                {/* Section 3: Root Cause Analysis */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1.5">
                    <Icon name="psychology" size={14} className="text-[#1F5A43]" />
                    <span>3. Root Cause Analysis & Diagnostic Findings</span>
                  </span>
                  <div className="p-4 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap font-sans">
                    {selectedReport.root_cause_analysis}
                  </div>
                </div>

                {/* Section 4: Advisory Recommendations */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1.5">
                    <Icon name="healing" size={14} className="text-[#1F5A43]" />
                    <span>4. Advisory Recommendations & Verification Steps</span>
                  </span>
                  <div className="p-4 rounded-lg bg-[#F5F3EE] border border-[#D8D5CD] text-[13px] text-[#171A18] leading-relaxed whitespace-pre-wrap font-sans">
                    {selectedReport.recommendations_summary}
                  </div>
                </div>

                {/* Section 5: Uncertainty & Telemetry Bounds */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider flex items-center gap-1.5">
                    <Icon name="warning" size={14} className="text-[#9A7440]" />
                    <span>5. Diagnostic Uncertainty & Telemetry Bounds</span>
                  </span>
                  <div className="p-4 rounded-lg bg-[#FEF9E7] border border-[#FAD7A0] text-[12.5px] text-[#9A7440] leading-relaxed whitespace-pre-wrap">
                    {selectedReport.uncertainty_disclaimer ||
                      "No explicit uncertainty statement recorded."}
                  </div>
                </div>
              </div>
            )}

            {/* Mode: Assembled Markdown View */}
            {reportViewMode === "markdown" && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-[11px] font-semibold text-[#5F6360] uppercase tracking-wider">
                  <span>Assembled Printable Markdown Document</span>
                  <span className="font-mono text-[10px]">
                    {selectedReport.markdown_content.length} characters
                  </span>
                </div>
                <div className="p-4 rounded-lg bg-[#171A18] text-[#F5F3EE] font-mono text-[12px] overflow-x-auto whitespace-pre-wrap leading-relaxed max-h-[600px] overflow-y-auto border border-[#2D312E]">
                  {selectedReport.markdown_content}
                </div>
              </div>
            )}

            {/* Safety & Invariant Footer */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#D8D5CD] pt-3 text-[11px] text-[#5F6360]">
              <div className="flex items-center gap-3">
                <span>Causality: unconfirmed (is_root_cause_confirmed: false)</span>
                <span>•</span>
                <span>Confidence Score: 0.0</span>
                <span>•</span>
                <span>Lifecycle: {selectedReport.status}</span>
              </div>
              <span className="font-mono text-[10px]">RootLens Post-Mortem Generator</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
