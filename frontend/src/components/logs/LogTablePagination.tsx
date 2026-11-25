"use client";

import React from "react";
import { Icon } from "@/components/ui/Icon";
import {
  DEFAULT_PAGE_SIZES,
  calculatePaginationRange,
} from "./tableUtils";

export interface LogTablePaginationProps {
  currentPage: number;
  totalPages: number;
  totalEvents: number;
  pageSize: number;
  onPageChange: (newPage: number) => void;
  onPageSizeChange: (newPageSize: number) => void;
  disabled?: boolean;
  className?: string;
}

export function LogTablePagination({
  currentPage,
  totalPages,
  totalEvents,
  pageSize,
  onPageChange,
  onPageSizeChange,
  disabled = false,
  className = "",
}: LogTablePaginationProps) {
  const { start, end } = calculatePaginationRange(
    currentPage,
    pageSize,
    totalEvents
  );
  const safeTotalPages = Math.max(1, totalPages);
  const hasPrevious = currentPage > 1 && !disabled;
  const hasNext = currentPage < safeTotalPages && !disabled;

  return (
    <div
      className={`px-4 py-3 bg-[#FAF8F5] border-t border-[#D8D5CD] flex flex-col sm:flex-row items-center justify-between gap-3 text-[12px] text-[#606366] ${className}`}
    >
      {/* Event Range Summary */}
      <div className="flex items-center gap-1.5">
        <span>Showing</span>
        <span className="font-mono font-semibold text-[#171A18]">
          {start.toLocaleString()}
        </span>
        <span>–</span>
        <span className="font-mono font-semibold text-[#171A18]">
          {end.toLocaleString()}
        </span>
        <span>of</span>
        <span className="font-mono font-semibold text-[#171A18]">
          {totalEvents.toLocaleString()}
        </span>
        <span>events</span>
      </div>

      <div className="flex items-center gap-3">
        {/* Page Size Selector */}
        <div className="flex items-center gap-1.5 text-[11px] text-[#606366]">
          <span>Per page:</span>
          <select
            value={pageSize}
            disabled={disabled}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            aria-label="Log records per page"
            className="bg-white border border-[#D8D5CD] rounded-md px-2 py-1 text-[11px] font-mono text-[#171A18] outline-none cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {DEFAULT_PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </div>

        {/* Page Jump Controls */}
        <div className="flex items-center gap-1">
          {/* First Page */}
          <button
            type="button"
            disabled={!hasPrevious}
            onClick={() => onPageChange(1)}
            aria-label="First page"
            title="First page"
            className={`p-1 rounded-md border flex items-center transition-colors ${
              hasPrevious
                ? "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F0EDE6] cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                : "bg-[#F5F3EE] border-[#E4E0D6] text-[#8A8780] cursor-not-allowed opacity-50"
            }`}
          >
            <Icon name="first_page" size={16} />
          </button>

          {/* Previous Page */}
          <button
            type="button"
            disabled={!hasPrevious}
            onClick={() => onPageChange(currentPage - 1)}
            aria-label="Previous page"
            title="Previous page"
            className={`p-1 rounded-md border flex items-center transition-colors ${
              hasPrevious
                ? "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F0EDE6] cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                : "bg-[#F5F3EE] border-[#E4E0D6] text-[#8A8780] cursor-not-allowed opacity-50"
            }`}
          >
            <Icon name="chevron_left" size={16} />
          </button>

          {/* Current Page Display */}
          <span className="px-2 font-mono text-[11px] font-semibold text-[#171A18]">
            {currentPage} / {safeTotalPages}
          </span>

          {/* Next Page */}
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => onPageChange(currentPage + 1)}
            aria-label="Next page"
            title="Next page"
            className={`p-1 rounded-md border flex items-center transition-colors ${
              hasNext
                ? "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F0EDE6] cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                : "bg-[#F5F3EE] border-[#E4E0D6] text-[#8A8780] cursor-not-allowed opacity-50"
            }`}
          >
            <Icon name="chevron_right" size={16} />
          </button>

          {/* Last Page */}
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => onPageChange(safeTotalPages)}
            aria-label="Last page"
            title="Last page"
            className={`p-1 rounded-md border flex items-center transition-colors ${
              hasNext
                ? "bg-white border-[#D8D5CD] text-[#171A18] hover:bg-[#F0EDE6] cursor-pointer focus-visible:ring-2 focus-visible:ring-[#1F5A43]"
                : "bg-[#F5F3EE] border-[#E4E0D6] text-[#8A8780] cursor-not-allowed opacity-50"
            }`}
          >
            <Icon name="last_page" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

