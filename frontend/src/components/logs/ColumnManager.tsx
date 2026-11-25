"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Icon } from "@/components/ui/Icon";
import {
  ColumnId,
  ColumnDefinition,
  AVAILABLE_COLUMNS,
  UserColumnPreferences,
  toggleColumnVisibility,
  reorderColumn,
  filterColumnDefinitions,
  saveStoredPreferences,
  resetStoredPreferences,
} from "./columnConfig";

export {
  toggleColumnVisibility,
  reorderColumn,
  filterColumnDefinitions,
};

export interface ColumnManagerProps {
  preferences: UserColumnPreferences;
  onPreferencesChange: (newPrefs: UserColumnPreferences) => void;
  className?: string;
}

export function ColumnManager({
  preferences,
  onPreferencesChange,
  className = "",
}: ColumnManagerProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Close popover and return focus to trigger button
  const handleClose = useCallback(() => {
    setIsOpen(false);
    setSearchQuery("");
    triggerRef.current?.focus();
  }, []);

  // Toggle popover state
  const handleToggle = () => {
    if (isOpen) {
      handleClose();
    } else {
      setIsOpen(true);
    }
  };

  // Keyboard navigation: Escape key closes popover and restores focus to trigger
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleClose();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, handleClose]);

  // Click outside listener: closes popover when clicking outside container
  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node;
      if (
        popoverRef.current &&
        !popoverRef.current.contains(target) &&
        triggerRef.current &&
        !triggerRef.current.contains(target)
      ) {
        handleClose();
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
    };
  }, [isOpen, handleClose]);

  // Focus management: move focus to search input upon opening
  useEffect(() => {
    if (isOpen) {
      // Small timeout ensures element is mounted and rendered before focus
      const timer = setTimeout(() => {
        searchInputRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  // Handle visibility toggle
  const handleToggleColumn = (columnId: ColumnId) => {
    const updated = toggleColumnVisibility(preferences, columnId);
    saveStoredPreferences(updated);
    onPreferencesChange(updated);
  };

  // Handle reorder
  const handleMoveColumn = (columnId: ColumnId, direction: "up" | "down") => {
    const updated = reorderColumn(preferences, columnId, direction);
    saveStoredPreferences(updated);
    onPreferencesChange(updated);
  };

  // Handle reset to factory defaults
  const handleResetDefaults = () => {
    const defaults = resetStoredPreferences();
    onPreferencesChange(defaults);
  };

  // Map columns in current ordered sequence
  const columnMap = new Map<ColumnId, ColumnDefinition>(
    AVAILABLE_COLUMNS.map((col) => [col.id, col])
  );

  const orderedColumns: ColumnDefinition[] = preferences.columnOrder
    .map((id) => columnMap.get(id))
    .filter((col): col is ColumnDefinition => Boolean(col));

  const filteredColumns = filterColumnDefinitions(orderedColumns, searchQuery);

  const visibleCount = preferences.visibleColumns.length;
  const totalCount = AVAILABLE_COLUMNS.length;

  return (
    <div className={`relative inline-block text-left ${className}`}>
      {/* Trigger Button */}
      <button
        ref={triggerRef}
        type="button"
        id="column-manager-trigger"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls="column-manager-popover"
        onClick={handleToggle}
        className={`px-3 py-1.5 rounded-lg border text-[12px] font-medium transition-colors flex items-center gap-1.5 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43] ${
          isOpen
            ? "bg-[#EBF2EE] border-[#1F5A43] text-[#1F5A43]"
            : "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F5F3EE] hover:border-[#8A8780]"
        }`}
        title="Customize visible columns and ordering"
      >
        <Icon name="view_column" size={16} className="text-[#1F5A43]" />
        <span>Columns</span>
        <span className="text-[11px] font-mono px-1.5 py-0.2 rounded bg-[#F5F3EE] text-[#606366] border border-[#D8D5CD]">
          {visibleCount}/{totalCount}
        </span>
      </button>

      {/* Popover Panel */}
      {isOpen && (
        <div
          ref={popoverRef}
          id="column-manager-popover"
          role="dialog"
          aria-label="Customize Table Columns"
          aria-modal="false"
          className="absolute right-0 top-full mt-2 w-84 sm:w-96 bg-white border border-[#D8D5CD] rounded-xl shadow-lg z-50 flex flex-col overflow-hidden text-[#171A18]"
        >
          {/* Header */}
          <div className="px-4 py-3 bg-[#F0EDE6] border-b border-[#D8D5CD] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Icon name="view_column" size={18} className="text-[#1F5A43]" />
              <div>
                <h3 className="text-[13px] font-semibold text-[#171A18] leading-tight">
                  Customize Columns
                </h3>
                <p className="text-[11px] text-[#606366]">
                  Toggle visibility or reorder table fields
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="p-1 rounded-md text-[#606366] hover:text-[#171A18] hover:bg-black/5 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
              aria-label="Close column manager"
            >
              <Icon name="close" size={16} />
            </button>
          </div>

          {/* Search Field */}
          <div className="p-3 border-b border-[#D8D5CD] bg-[#FAF8F5]">
            <div className="relative flex items-center">
              <span className="absolute left-2.5 text-[#8A8780] pointer-events-none flex items-center">
                <Icon name="search" size={15} />
              </span>
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search columns..."
                aria-label="Filter columns list"
                className="w-full pl-8 pr-7 py-1.5 text-[12px] bg-white border border-[#D8D5CD] rounded-lg outline-none text-[#171A18] placeholder-[#8A8780] focus-visible:ring-2 focus-visible:ring-[#1F5A43] focus-visible:border-transparent transition-all"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2 text-[#8A8780] hover:text-[#171A18] p-0.5 cursor-pointer"
                  aria-label="Clear column search"
                >
                  <Icon name="close" size={14} />
                </button>
              )}
            </div>
          </div>

          {/* Column Checkbox & Reorder List */}
          <div className="max-h-72 overflow-y-auto divide-y divide-[#D8D5CD] p-1">
            {filteredColumns.length === 0 ? (
              <div className="py-8 px-4 text-center text-[12px] text-[#606366]">
                No columns match &ldquo;{searchQuery}&rdquo;
              </div>
            ) : (
              filteredColumns.map((col) => {
                const isVisible = preferences.visibleColumns.includes(col.id);
                const isEssential = col.isEssential;
                const orderIndex = preferences.columnOrder.indexOf(col.id);
                const isFirst = orderIndex === 0;
                const isLast = orderIndex === preferences.columnOrder.length - 1;

                return (
                  <div
                    key={col.id}
                    className={`flex items-center justify-between p-2 rounded-lg transition-colors ${
                      isVisible ? "bg-white hover:bg-[#FAF8F5]" : "bg-black/[0.02] text-[#8A8780]"
                    }`}
                  >
                    {/* Checkbox and Label */}
                    <div className="flex items-start gap-2.5 min-w-0 pr-2">
                      <input
                        type="checkbox"
                        id={`col-toggle-${col.id}`}
                        checked={isVisible}
                        disabled={isEssential}
                        onChange={() => handleToggleColumn(col.id)}
                        className={`mt-0.5 h-4 w-4 rounded border-[#D8D5CD] text-[#1F5A43] accent-[#1F5A43] focus-visible:ring-2 focus-visible:ring-[#1F5A43] ${
                          isEssential ? "cursor-not-allowed opacity-70" : "cursor-pointer"
                        }`}
                        aria-describedby={`col-desc-${col.id}`}
                      />
                      <label
                        htmlFor={`col-toggle-${col.id}`}
                        className={`flex flex-col min-w-0 ${
                          isEssential ? "cursor-default" : "cursor-pointer"
                        }`}
                      >
                        <span className="flex items-center gap-1.5 text-[12px] font-medium leading-tight text-[#171A18]">
                          <span>{col.label}</span>
                          {isEssential && (
                            <span
                              className="text-[10px] font-mono px-1 py-0.2 rounded bg-[#EBF2EE] text-[#1F5A43] border border-[#1F5A43]/20 flex items-center gap-0.5"
                              title="Essential column cannot be hidden"
                            >
                              <Icon name="lock" size={10} />
                              <span>Required</span>
                            </span>
                          )}
                        </span>
                        <span
                          id={`col-desc-${col.id}`}
                          className="text-[11px] text-[#606366] leading-tight truncate mt-0.5"
                        >
                          {col.description}
                        </span>
                      </label>
                    </div>

                    {/* Reorder Buttons (Up / Down) */}
                    <div className="flex items-center gap-0.5 shrink-0 pl-1">
                      <button
                        type="button"
                        disabled={isFirst || Boolean(searchQuery)}
                        onClick={() => handleMoveColumn(col.id, "up")}
                        aria-label={`Move ${col.label} up`}
                        title={
                          searchQuery
                            ? "Clear search to reorder"
                            : isFirst
                            ? "Already at top"
                            : `Move ${col.label} up`
                        }
                        className={`p-1 rounded text-[#606366] transition-colors ${
                          isFirst || Boolean(searchQuery)
                            ? "opacity-30 cursor-not-allowed"
                            : "hover:text-[#171A18] hover:bg-black/5 cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                        }`}
                      >
                        <Icon name="keyboard_arrow_up" size={16} />
                      </button>
                      <button
                        type="button"
                        disabled={isLast || Boolean(searchQuery)}
                        onClick={() => handleMoveColumn(col.id, "down")}
                        aria-label={`Move ${col.label} down`}
                        title={
                          searchQuery
                            ? "Clear search to reorder"
                            : isLast
                            ? "Already at bottom"
                            : `Move ${col.label} down`
                        }
                        className={`p-1 rounded text-[#606366] transition-colors ${
                          isLast || Boolean(searchQuery)
                            ? "opacity-30 cursor-not-allowed"
                            : "hover:text-[#171A18] hover:bg-black/5 cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                        }`}
                      >
                        <Icon name="keyboard_arrow_down" size={16} />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer Actions */}
          <div className="p-3 bg-[#F0EDE6] border-t border-[#D8D5CD] flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={handleResetDefaults}
              className="text-[11px] font-medium text-[#606366] hover:text-[#171A18] transition-colors flex items-center gap-1 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43] rounded px-1.5 py-1"
            >
              <Icon name="restart_alt" size={14} />
              <span>Reset to Defaults</span>
            </button>
            <button
              type="button"
              onClick={handleClose}
              className="px-3 py-1 rounded-lg bg-[#1F5A43] hover:bg-[#184735] text-white text-[12px] font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
