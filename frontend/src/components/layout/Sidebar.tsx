"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/ui/Icon";

interface SidebarProps {
  onCloseMobile?: () => void;
}

export function Sidebar({ onCloseMobile }: SidebarProps) {
  const pathname = usePathname();
  const [logsExpanded, setLogsExpanded] = useState(true);

  const isLogsActive = pathname.startsWith("/logs");
  const isDashboardActive = pathname === "/dashboard" || pathname === "/";
  const isIncidentsActive = pathname.startsWith("/incidents");
  const isAiInvestigationActive = pathname.startsWith("/ai-investigation");
  const isReportsActive = pathname.startsWith("/reports");
  const isSettingsActive = pathname.startsWith("/settings");

  return (
    <aside className="h-full w-64 bg-white border-r border-[#EDEFF3] flex flex-col justify-between overflow-hidden select-none">
      {/* Brand & Navigation */}
      <div className="flex flex-col flex-1 min-h-0 overflow-y-auto">
        {/* Header / Brand */}
        <div className="h-14 px-4 flex items-center justify-between border-b border-[#EDEFF3] shrink-0">
          <Link
            href="/dashboard"
            onClick={onCloseMobile}
            className="flex items-center gap-2 focus:outline-none focus:ring-2 focus:ring-[#4D80FC] rounded-lg"
          >
            <div className="w-7 h-7 rounded-lg bg-[#4D80FC] flex items-center justify-center text-white shadow-xs shrink-0">
              <Icon name="hub" size={17} />
            </div>
            <span className="font-semibold text-[15px] text-[#1B1A1A] tracking-tight">
              RootLens
            </span>
          </Link>
          <span className="px-1.5 py-0.5 rounded font-mono text-[10px] text-[#9B9FA3] bg-[#F0F2F5] font-medium shrink-0">
            v2.4
          </span>
        </div>

        {/* Navigation list */}
        <nav className="flex flex-col gap-1 px-3 pt-4">
          {/* Dashboard */}
          <Link
            href="/dashboard"
            onClick={onCloseMobile}
            className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition-colors ${
              isDashboardActive
                ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
            }`}
          >
            <Icon
              name="dashboard"
              size={18}
              className={isDashboardActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
            />
            <span>Dashboard</span>
          </Link>

          {/* Logs Menu with subitems */}
          <div className="flex flex-col">
            <button
              type="button"
              onClick={() => setLogsExpanded((prev) => !prev)}
              className={`flex items-center justify-between px-3 py-2 rounded-lg text-[13px] transition-colors cursor-pointer ${
                isLogsActive
                  ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                  : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Icon
                  name="terminal"
                  size={18}
                  className={isLogsActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
                />
                <span>Logs</span>
              </div>
              <Icon
                name={logsExpanded ? "expand_less" : "expand_more"}
                size={16}
                className={isLogsActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
              />
            </button>

            {logsExpanded && (
              <div className="flex flex-col pl-8 pr-1 py-1 gap-0.5">
                <Link
                  href="/logs"
                  onClick={onCloseMobile}
                  className={`px-2.5 py-1.5 rounded-lg text-[13px] transition-colors ${
                    pathname === "/logs"
                      ? "bg-[#F0F4FF] text-[#4D80FC] font-semibold"
                      : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
                  }`}
                >
                  Log Explorer
                </Link>
                <Link
                  href="/logs/upload"
                  onClick={onCloseMobile}
                  className={`px-2.5 py-1.5 rounded-lg text-[12px] transition-colors ${
                    pathname === "/logs/upload"
                      ? "bg-[#F0F4FF] text-[#4D80FC] font-semibold"
                      : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
                  }`}
                >
                  Upload Logs
                </Link>
              </div>
            )}
          </div>

          {/* Incidents */}
          <Link
            href="/incidents"
            onClick={onCloseMobile}
            className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition-colors ${
              isIncidentsActive
                ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
            }`}
          >
            <Icon
              name="warning"
              size={18}
              className={isIncidentsActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
            />
            <span>Incidents</span>
          </Link>

          {/* AI Investigation */}
          <Link
            href="/ai-investigation"
            onClick={onCloseMobile}
            className={`flex items-center justify-between px-3 py-2 rounded-lg text-[13px] transition-colors ${
              isAiInvestigationActive
                ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <Icon
                name="insights"
                size={18}
                className={isAiInvestigationActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
              />
              <span>AI Investigation</span>
            </div>
          </Link>

          {/* Reports */}
          <Link
            href="/reports"
            onClick={onCloseMobile}
            className={`flex items-center justify-between px-3 py-2 rounded-lg text-[13px] transition-colors ${
              isReportsActive
                ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <Icon
                name="summarize"
                size={18}
                className={isReportsActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
              />
              <span>Reports</span>
            </div>
          </Link>

          {/* Settings */}
          <Link
            href="/settings"
            onClick={onCloseMobile}
            className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition-colors ${
              isSettingsActive
                ? "bg-[#EEF3FF] text-[#4D80FC] font-semibold"
                : "text-[#606366] hover:bg-[#F5F6F9] hover:text-[#1B1A1A] font-medium"
            }`}
          >
            <Icon
              name="settings"
              size={18}
              className={isSettingsActive ? "text-[#4D80FC]" : "text-[#9B9FA3]"}
            />
            <span>Settings</span>
          </Link>
        </nav>
      </div>

      {/* Footer System Status & User Profile */}
      <div className="p-3 flex flex-col gap-2 border-t border-[#EDEFF3] bg-white shrink-0">
        <div className="flex items-center justify-between px-3 py-2 bg-[#F9FAFC] rounded-lg border border-[#EDEFF3]">
          <div className="flex items-center gap-2">
            <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
            <span className="text-[12px] text-[#606366] font-medium">System Status</span>
          </div>
          <span className="font-mono text-[11px] font-medium text-[#1B1A1A]">Operational</span>
        </div>

        <div className="flex items-center justify-between p-2 rounded-lg hover:bg-[#F9FAFC] transition-colors">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full bg-[#EEF3FF] border border-[#BFDBFE] flex items-center justify-center text-[12px] text-[#4D80FC] font-semibold shrink-0">
              RL
            </div>
            <div className="flex flex-col leading-tight">
              <span className="text-[13px] text-[#1B1A1A] font-semibold">RootLens Operator</span>
              <span className="font-mono text-[11px] text-[#9B9FA3]">Platform Operator</span>
            </div>
          </div>
          <Icon name="more_vert" size={18} className="text-[#9B9FA3]" />
        </div>
      </div>
    </aside>
  );
}
