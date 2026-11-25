"use client";

import React, { useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";

export type IncidentTabId =
  | "overview"
  | "timeline"
  | "investigate"
  | "recommendations"
  | "reports";

export interface IncidentTabNavProps {
  activeTab: IncidentTabId;
  onTabChange: (tab: IncidentTabId) => void;
  timelineEventsCount?: number;
  evidenceItemsCount?: number;
  recommendationsCount?: number;
  reportsCount?: number;
  disabledTabs?: IncidentTabId[];
  className?: string;
}

interface TabItem {
  id: IncidentTabId;
  label: string;
  icon: string;
  badge?: number | string | null;
  badgeAria?: string;
  disabled?: boolean;
}

export function IncidentTabNav({
  activeTab,
  onTabChange,
  timelineEventsCount,
  evidenceItemsCount,
  recommendationsCount,
  reportsCount,
  disabledTabs = ["investigate", "recommendations", "reports"],
  className = "",
}: IncidentTabNavProps) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Compute timeline badge combining events and evidence items
  let timelineBadge: number | string | null = null;
  let timelineBadgeAria: string | undefined = undefined;

  if (timelineEventsCount !== undefined && evidenceItemsCount !== undefined) {
    timelineBadge = timelineEventsCount + evidenceItemsCount;
    timelineBadgeAria = `${timelineEventsCount} timeline events, ${evidenceItemsCount} evidence items`;
  } else if (timelineEventsCount !== undefined) {
    timelineBadge = timelineEventsCount;
    timelineBadgeAria = `${timelineEventsCount} timeline events`;
  } else if (evidenceItemsCount !== undefined) {
    timelineBadge = evidenceItemsCount;
    timelineBadgeAria = `${evidenceItemsCount} evidence items`;
  }

  const tabs: TabItem[] = [
    {
      id: "overview",
      label: "Overview & RCA",
      icon: "psychology",
      disabled: disabledTabs.includes("overview"),
    },
    {
      id: "timeline",
      label: "Timeline & Evidence",
      icon: "timeline",
      badge: timelineBadge,
      badgeAria: timelineBadgeAria,
      disabled: disabledTabs.includes("timeline"),
    },
    {
      id: "investigate",
      label: "AI Investigation",
      icon: "chat",
      disabled: disabledTabs.includes("investigate"),
    },
    {
      id: "recommendations",
      label: "Recommendations",
      icon: "task_alt",
      badge: recommendationsCount !== undefined ? recommendationsCount : null,
      badgeAria:
        recommendationsCount !== undefined
          ? `${recommendationsCount} recommendations`
          : undefined,
      disabled: disabledTabs.includes("recommendations"),
    },
    {
      id: "reports",
      label: "Post-Mortem Reports",
      icon: "summarize",
      badge: reportsCount !== undefined ? reportsCount : null,
      badgeAria:
        reportsCount !== undefined ? `${reportsCount} reports` : undefined,
      disabled: disabledTabs.includes("reports"),
    },
  ];

  // Roving tabIndex state for manual activation:
  // Tracks which tab button holds keyboard focus (tabIndex=0)
  const [prevActiveTab, setPrevActiveTab] = useState(activeTab);
  const [focusedIndex, setFocusedIndex] = useState<number>(() => {
    const idx = tabs.findIndex((t) => t.id === activeTab);
    return idx >= 0 ? idx : 0;
  });

  // Synchronize roving tabIndex when activeTab prop changes
  if (activeTab !== prevActiveTab) {
    setPrevActiveTab(activeTab);
    const idx = tabs.findIndex((t) => t.id === activeTab);
    if (idx >= 0) {
      setFocusedIndex(idx);
    }
  }

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLButtonElement>,
    currentIndex: number
  ) => {
    let nextIndex = currentIndex;
    const lastIndex = tabs.length - 1;

    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
        e.preventDefault();
        nextIndex = currentIndex >= lastIndex ? 0 : currentIndex + 1;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        e.preventDefault();
        nextIndex = currentIndex <= 0 ? lastIndex : currentIndex - 1;
        break;
      case "Home":
        e.preventDefault();
        nextIndex = 0;
        break;
      case "End":
        e.preventDefault();
        nextIndex = lastIndex;
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        // Enter / Space activates the focused tab only if not disabled
        if (!tabs[currentIndex].disabled) {
          onTabChange(tabs[currentIndex].id);
        }
        return;
      default:
        return;
    }

    // Manual tab activation: Arrow keys and Home/End move FOCUS only
    setFocusedIndex(nextIndex);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <nav
      aria-label="Incident Investigation Sections"
      className={`w-full border-b border-[#D8D5CD] bg-[#F5F3EE] ${className}`}
    >
      <div
        role="tablist"
        aria-orientation="horizontal"
        className="flex items-center gap-1.5 overflow-x-auto scrollbar-none px-4 pt-2 -mb-px"
      >
        {tabs.map((tab, idx) => {
          const isActive = activeTab === tab.id;
          const isFocused = focusedIndex === idx;
          const isDisabled = Boolean(tab.disabled);

          return (
            <button
              key={tab.id}
              ref={(el) => {
                tabRefs.current[idx] = el;
              }}
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={isActive}
              aria-controls={`panel-${tab.id}`}
              aria-disabled={isDisabled ? true : undefined}
              tabIndex={isFocused ? 0 : -1}
              onFocus={() => setFocusedIndex(idx)}
              onClick={() => {
                if (isDisabled) return;
                setFocusedIndex(idx);
                onTabChange(tab.id);
              }}
              onKeyDown={(e) => handleKeyDown(e, idx)}
              type="button"
              className={`group inline-flex items-center gap-2 px-3.5 py-2.5 text-[13px] rounded-t-lg border-b-2 font-medium transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43] focus-visible:ring-offset-1 ${
                isDisabled
                  ? "border-transparent text-[#9B9FA3] opacity-55 cursor-not-allowed bg-transparent"
                  : isActive
                  ? "border-[#1F5A43] text-[#1F5A43] font-semibold bg-white shadow-xs cursor-pointer"
                  : "border-transparent text-[#5F6360] hover:text-[#171A18] hover:bg-[#EAE7DF] cursor-pointer"
              }`}
            >
              <Icon
                name={tab.icon}
                size={17}
                className={
                  isDisabled
                    ? "text-[#9B9FA3]"
                    : isActive
                    ? "text-[#1F5A43]"
                    : "text-[#5F6360] group-hover:text-[#171A18]"
                }
              />
              <span>{tab.label}</span>
              {isDisabled && (
                <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-[#D8D5CD]/50 text-[#5F6360] uppercase tracking-wider">
                  Upcoming
                </span>
              )}
              {!isDisabled && tab.badge !== undefined && tab.badge !== null && (
                <span
                  className={`inline-flex items-center justify-center px-1.5 py-0.5 rounded-full text-[10px] font-mono font-medium ${
                    isActive
                      ? "bg-[#1F5A43] text-white"
                      : "bg-[#D8D5CD] text-[#171A18] group-hover:bg-[#C9C5BC]"
                  }`}
                  aria-label={tab.badgeAria || `${tab.badge} items`}
                  title={tab.badgeAria}
                >
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
