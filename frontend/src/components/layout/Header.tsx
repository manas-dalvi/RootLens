"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { fetchSystemReadiness } from "@/lib/api/dashboard";

interface HeaderProps {
  onToggleMobileSidebar: () => void;
}

export function Header({ onToggleMobileSidebar }: HeaderProps) {
  const [osStatus, setOsStatus] = useState<"checking" | "connected" | "unavailable">("checking");
  const [clusterName, setClusterName] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [secondsAgo, setSecondsAgo] = useState<number>(0);
  const [isDocsOpen, setIsDocsOpen] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;

    async function checkReadiness() {
      await Promise.resolve();
      if (cancelled) return;
      try {
        const readiness = await fetchSystemReadiness();
        if (cancelled) return;
        const os = readiness.dependencies?.opensearch;
        if (os?.status === "available") {
          setOsStatus("connected");
          setClusterName(os.cluster_name || null);
        } else {
          setOsStatus("unavailable");
        }
      } catch {
        if (!cancelled) {
          setOsStatus("unavailable");
        }
      }
    }

    checkReadiness();

    return () => {
      cancelled = true;
    };
  }, []);

  // Sync refresh state with dashboard and other active pages
  useEffect(() => {
    function handleRefreshStatus(e: Event) {
      const customEvent = e as CustomEvent<{ isRefreshing: boolean }>;
      const refreshing = Boolean(customEvent.detail?.isRefreshing);
      setIsRefreshing(refreshing);
      if (!refreshing) {
        setSecondsAgo(0);
      }
    }

    window.addEventListener("rootlens:refresh-status", handleRefreshStatus);
    return () => {
      window.removeEventListener("rootlens:refresh-status", handleRefreshStatus);
    };
  }, []);

  // Seconds counter for sync status indicator
  useEffect(() => {
    const timer = setInterval(() => {
      setSecondsAgo((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const handleGlobalRefresh = () => {
    setIsRefreshing(true);
    window.dispatchEvent(new CustomEvent("rootlens:refresh"));
  };

  return (
    <>
      <header className="fixed top-0 left-0 md:left-64 right-0 h-14 bg-white z-40 flex items-center justify-between px-3 sm:px-6 border-b border-[#EDEFF3] gap-2 max-w-full">
        {/* Left section: mobile hamburger + cluster indicator */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={onToggleMobileSidebar}
            aria-label="Toggle navigation menu"
            className="md:hidden flex items-center justify-center p-1.5 rounded-lg text-[#606366] hover:text-[#1B1A1A] hover:bg-[#F5F6F9] border border-[#EDEFF3] transition-colors"
          >
            <Icon name="menu" size={18} />
          </button>

          <div className="flex items-center gap-2 text-[12px] text-[#606366] whitespace-nowrap">
            <span
              className={`inline-flex items-center gap-1.5 font-mono text-[11px] px-2 py-1 rounded-md border ${
                osStatus === "connected"
                  ? "text-[#057A55] bg-[#DEF7EC] border-[#BCF0DA]"
                  : osStatus === "unavailable"
                  ? "text-[#606366] bg-[#F5F6F9] border-[#EDEFF3]"
                  : "text-[#9B9FA3] bg-[#F5F6F9] border-[#EDEFF3]"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  osStatus === "connected"
                    ? "bg-[#31C48D]"
                    : osStatus === "unavailable"
                    ? "bg-[#9B9FA3]"
                    : "bg-[#9B9FA3] animate-pulse"
                }`}
              />
              <span>
                {osStatus === "checking" ? (
                  "Checking..."
                ) : osStatus === "connected" ? (
                  <>
                    <span className="hidden sm:inline">
                      Cluster: Connected{clusterName ? ` (${clusterName})` : ""}
                    </span>
                    <span className="sm:hidden">Connected</span>
                  </>
                ) : (
                  "Offline"
                )}
              </span>
            </span>
          </div>
        </div>

        {/* Center: Search trigger */}
        <div className="hidden lg:flex items-center flex-1 max-w-sm xl:max-w-md mx-3 xl:mx-6 min-w-0">
          <Link
            href="/logs"
            className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-[#606366] hover:border-[#BFDBFE] hover:bg-white transition-all cursor-pointer"
          >
            <Icon name="search" size={16} className="text-[#9B9FA3]" />
            <span className="text-[12px] text-[#9B9FA3] flex-1 truncate">
              Search logs, incidents, services...
            </span>
            <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-white border border-[#EDEFF3] text-[#606366] font-medium shadow-xs shrink-0">
              ⌘K
            </span>
          </Link>
        </div>

        {/* Right controls: Documentation & Header-Level Refresh */}
        <div className="flex items-center gap-1.5 sm:gap-2.5 shrink-0">
          {/* Documentation modal trigger */}
          <button
            type="button"
            onClick={() => setIsDocsOpen(true)}
            className="flex items-center gap-1 sm:gap-1.5 p-1.5 sm:px-2.5 sm:py-1.5 rounded-lg text-[#606366] hover:text-[#1B1A1A] hover:bg-[#F5F6F9] text-[12px] font-medium transition-colors cursor-pointer"
            title="View RootLens Architecture & System Documentation"
          >
            <Icon name="description" size={16} className="text-[#9B9FA3]" />
            <span className="hidden sm:inline">Docs</span>
          </button>

          {/* Sync status text indicator */}
          <span className="font-mono text-[11px] text-[#9B9FA3] hidden lg:inline select-none">
            {isRefreshing
              ? "Syncing..."
              : secondsAgo === 0
              ? "Synced just now"
              : `Synced ${secondsAgo}s ago`}
          </span>

          {/* Top-Right Header Refresh Action */}
          <button
            type="button"
            onClick={handleGlobalRefresh}
            disabled={isRefreshing}
            className="flex items-center gap-1 sm:gap-1.5 p-1.5 sm:px-3 sm:py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[12px] font-medium transition-colors active:scale-95 disabled:opacity-60 cursor-pointer shadow-2xs"
            title="Refresh active dashboard telemetry and cluster status"
          >
            <Icon
              name="refresh"
              size={15}
              className={`text-[#606366] transition-transform duration-500 ${
                isRefreshing ? "animate-spin text-[#4D80FC]" : ""
              }`}
            />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </header>

      {/* Interactive In-App Documentation Modal */}
      {isDocsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
          <div
            className="bg-white rounded-xl max-w-2xl w-full border border-[#EDEFF3] shadow-2xl max-h-[85vh] flex flex-col overflow-hidden animate-in fade-in duration-150"
            role="dialog"
            aria-modal="true"
            aria-labelledby="docs-modal-title"
          >
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-[#EDEFF3] flex items-center justify-between bg-white shrink-0">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-[#EEF3FF] border border-[#BFDBFE] flex items-center justify-center text-[#4D80FC]">
                  <Icon name="menu_book" size={18} />
                </div>
                <div>
                  <h3 id="docs-modal-title" className="text-[15px] font-semibold text-[#1B1A1A]">
                    RootLens Technical Documentation
                  </h3>
                  <p className="text-[11px] text-[#606366]">
                    Architecture Overview, Telemetry Pipeline, and Operational Runbook
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsDocsOpen(false)}
                className="p-1 rounded-lg text-[#9B9FA3] hover:text-[#1B1A1A] hover:bg-[#F5F6F9] transition-colors"
                aria-label="Close documentation modal"
              >
                <Icon name="close" size={18} />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-y-auto space-y-5 text-[13px] text-[#1B1A1A] leading-relaxed custom-scrollbar">
              <section>
                <h4 className="font-semibold text-[13px] text-[#1B1A1A] mb-1.5 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#4D80FC]" />
                  System Overview
                </h4>
                <p className="text-[#606366] text-[12px]">
                  RootLens is an AI-powered enterprise observability and automated root cause analysis platform. It correlates multi-service error bursts, models sequence anomalies via LogBERT, and generates evidence-grounded incident diagnoses.
                </p>
              </section>

              <section className="bg-[#F9FAFC] p-4 rounded-lg border border-[#EDEFF3]">
                <h4 className="font-semibold text-[13px] text-[#1B1A1A] mb-2">
                  Telemetry Ingestion Pipeline
                </h4>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono text-[11px]">
                  <div className="bg-white p-2.5 rounded border border-[#EDEFF3]">
                    <div className="font-semibold text-[#4D80FC]">1. Ingestion</div>
                    <div className="text-[#606366] text-[10px] mt-1">Multi-format log uploads (.csv, .json, .log, .syslog)</div>
                  </div>
                  <div className="bg-white p-2.5 rounded border border-[#EDEFF3]">
                    <div className="font-semibold text-[#4D80FC]">2. Normalization</div>
                    <div className="text-[#606366] text-[10px] mt-1">Schema mapping &amp; OpenSearch monthly partition indexing</div>
                  </div>
                  <div className="bg-white p-2.5 rounded border border-[#EDEFF3]">
                    <div className="font-semibold text-[#4D80FC]">3. Correlation &amp; RCA</div>
                    <div className="text-[#606366] text-[10px] mt-1">15m sliding window clustering &amp; LogBERT anomaly scoring</div>
                  </div>
                </div>
              </section>

              <section>
                <h4 className="font-semibold text-[13px] text-[#1B1A1A] mb-1.5 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#31C48D]" />
                  Key API Endpoints
                </h4>
                <ul className="space-y-1 font-mono text-[11px] text-[#606366]">
                  <li className="flex items-center justify-between p-1.5 rounded bg-[#F5F6F9]">
                    <span className="text-[#1B1A1A]">GET /api/v1/dashboard/metrics</span>
                    <span className="text-[#9B9FA3]">Cluster KPIs &amp; health</span>
                  </li>
                  <li className="flex items-center justify-between p-1.5 rounded bg-[#F5F6F9]">
                    <span className="text-[#1B1A1A]">GET /api/v1/logs</span>
                    <span className="text-[#9B9FA3]">OpenSearch log search DSL</span>
                  </li>
                  <li className="flex items-center justify-between p-1.5 rounded bg-[#F5F6F9]">
                    <span className="text-[#1B1A1A]">GET /api/v1/logs/aggregations</span>
                    <span className="text-[#9B9FA3]">Time-bucketed histograms</span>
                  </li>
                  <li className="flex items-center justify-between p-1.5 rounded bg-[#F5F6F9]">
                    <span className="text-[#1B1A1A]">GET /api/v1/incidents</span>
                    <span className="text-[#9B9FA3]">Incident catalogue &amp; triage</span>
                  </li>
                </ul>
              </section>
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-3.5 border-t border-[#EDEFF3] flex items-center justify-between bg-[#F9FAFC] shrink-0">
              <Link
                href="/reports"
                onClick={() => setIsDocsOpen(false)}
                className="text-[12px] text-[#4D80FC] hover:text-[#3B6EE8] font-medium flex items-center gap-1"
              >
                <span>View Incident Reports</span>
                <Icon name="arrow_forward" size={14} />
              </Link>
              <button
                type="button"
                onClick={() => setIsDocsOpen(false)}
                className="px-4 py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[12px] font-medium transition-colors cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
