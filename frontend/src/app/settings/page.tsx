"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { API_BASE_URL } from "@/config/api";
import { fetchSystemReadiness, SystemReadiness } from "@/lib/api/dashboard";
import { ApiError } from "@/lib/api/logs";

export default function SettingsPage() {
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true);
    setRefreshTrigger((prev) => prev + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function execute() {
      // Defer state update to microtask boundary
      await Promise.resolve();
      if (cancelled) return;

      if (refreshTrigger === 0) {
        setIsLoading(true);
      }
      setError(null);

      try {
        const data = await fetchSystemReadiness(controller.signal);
        if (cancelled) return;
        setReadiness(data);
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError) {
          setError(err.message || "Failed to connect to backend diagnostics endpoint.");
        } else {
          setError("An unexpected error occurred while fetching system diagnostics.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    }

    execute();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [refreshTrigger]);

  const osInfo = readiness?.dependencies?.opensearch;
  const pgInfo = readiness?.dependencies?.postgres;

  return (
    <div className="flex flex-col w-full p-4 lg:p-6 gap-6">
      {/* Header bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-2 border-b border-[#EDEFF3]">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-3">
            <h1 className="text-[22px] font-semibold text-[#1B1A1A] tracking-tight">
              Settings &amp; Diagnostics
            </h1>
            <StatusBadge label="READ-ONLY DIAGNOSTICS" variant="neutral" />
          </div>
          <p className="text-[13px] text-[#606366]">
            Active runtime configurations, connection endpoints, and backing store health.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleRefresh}
            disabled={isRefreshing || isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[13px] font-medium transition-colors disabled:opacity-60 cursor-pointer"
          >
            <Icon
              name="refresh"
              size={16}
              className={`text-[#606366] transition-transform duration-500 ${
                isRefreshing ? "animate-spin text-[#4D80FC]" : ""
              }`}
            />
            <span>Refresh Diagnostics</span>
          </button>
          <Link
            href="/dashboard"
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[#4D80FC] hover:bg-[#3F70E8] text-white text-[13px] font-medium transition-colors shadow-xs"
          >
            <Icon name="dashboard" size={16} />
            <span>Dashboard</span>
          </Link>
        </div>
      </div>

      {/* Error state if backend diagnostics fail */}
      {error && (
        <div className="p-4 rounded-xl bg-[#FEECEB] border border-[#FCD3CE] flex items-start justify-between gap-3 text-[#E63316]">
          <div className="flex items-start gap-2.5">
            <Icon name="error" size={20} className="shrink-0 mt-0.5" />
            <div className="flex flex-col gap-0.5">
              <span className="font-semibold text-[13px]">
                Diagnostics Unavailable
              </span>
              <p className="text-[12px] text-[#606366]">{error}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleRefresh}
            className="px-3 py-1 bg-white border border-[#FCD3CE] rounded-lg text-[12px] font-medium text-[#E63316] hover:bg-[#FEECEB] transition-colors shrink-0 cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}

      {/* Grid: Diagnostics & Runtime Configuration */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Panel 1: API & Client Runtime */}
        <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] flex flex-col gap-4">
          <div className="flex items-center justify-between pb-1 border-b border-[#EDEFF3]">
            <div className="flex items-center gap-2">
              <Icon name="dns" size={18} className="text-[#4D80FC]" />
              <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                Client &amp; API Configuration
              </h2>
            </div>
            <StatusBadge label="ACTIVE" variant="healthy" dot />
          </div>

          <div className="flex flex-col gap-3 font-mono text-[12px]">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">API Base URL</span>
              <span className="text-[#1B1A1A] font-semibold break-all">{API_BASE_URL}</span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">Client Shell Version</span>
              <span className="text-[#1B1A1A] font-semibold">v2.4 (Stitch Observability)</span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">Backend Application</span>
              <span className="text-[#1B1A1A] font-semibold">
                {isLoading ? "Checking..." : readiness ? `${readiness.app} (v${readiness.version})` : "Not reachable"}
              </span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">Deployment Environment</span>
              <span className="text-[#1B1A1A] font-semibold">
                {isLoading ? "Checking..." : readiness?.environment || "development"}
              </span>
            </div>
          </div>
        </div>

        {/* Panel 2: Backing Store Diagnostics */}
        <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] flex flex-col gap-4">
          <div className="flex items-center justify-between pb-1 border-b border-[#EDEFF3]">
            <div className="flex items-center gap-2">
              <Icon name="database" size={18} className="text-[#4D80FC]" />
              <h2 className="text-[15px] font-semibold text-[#1B1A1A]">
                Backing Store Readiness
              </h2>
            </div>
            {readiness?.status === "ready" ? (
              <StatusBadge label="READY" variant="healthy" dot />
            ) : readiness?.status === "degraded" ? (
              <StatusBadge label="DEGRADED" variant="warning" dot />
            ) : (
              <StatusBadge label="PENDING" variant="neutral" />
            )}
          </div>

          <div className="flex flex-col gap-3 font-mono text-[12px]">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">OpenSearch Status</span>
              <span className="text-[#1B1A1A] font-semibold">
                {isLoading
                  ? "Checking..."
                  : osInfo?.status
                  ? `${osInfo.status.toUpperCase()} (${osInfo.cluster_name || "cluster"}, v${osInfo.version || "unknown"})`
                  : "Unavailable"}
              </span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">PostgreSQL Status</span>
              <span className="text-[#1B1A1A] font-semibold">
                {isLoading
                  ? "Checking..."
                  : pgInfo?.status
                  ? `${pgInfo.status.toUpperCase()} (database: ${pgInfo.database || "rootlens"})`
                  : "Unavailable"}
              </span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">Index Naming Pattern</span>
              <span className="text-[#1B1A1A] font-semibold">rootlens-logs-YYYY.MM</span>
            </div>

            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
              <span className="text-[#606366] font-sans text-[12px]">Cluster UUID</span>
              <span className="text-[#1B1A1A] font-semibold truncate max-w-[200px]" title={osInfo?.cluster_uuid}>
                {isLoading ? "Checking..." : osInfo?.cluster_uuid || "Not exposed by current API"}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Administrative Notice */}
      <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Icon name="admin_panel_settings" size={18} className="text-[#606366]" />
          <h2 className="text-[14px] font-semibold text-[#1B1A1A]">
            Administrative Configuration Notice
          </h2>
        </div>
        <p className="text-[12px] text-[#606366] leading-relaxed">
          Mutable configuration controls—including log retention lifecycle policies, user authentication/RBAC, alert routing destinations, and parsing schema overrides—are scheduled for administrative phases. Connection settings on this page are strictly read-only diagnostics derived from active environment variables and backend readiness probes.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1 text-[11px] font-mono text-[#9B9FA3]">
          <div className="p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
            <span>Retention Lifecycle: </span>
            <span className="text-[#606366] font-sans">Not exposed by current API</span>
          </div>
          <div className="p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
            <span>RBAC &amp; Roles: </span>
            <span className="text-[#606366] font-sans">Not exposed by current API</span>
          </div>
          <div className="p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3]">
            <span>Alerting Webhooks: </span>
            <span className="text-[#606366] font-sans">Not exposed by current API</span>
          </div>
        </div>
      </div>
    </div>
  );
}
