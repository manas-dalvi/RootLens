"use client";

import React, { useEffect, useRef } from "react";
import { Icon } from "@/components/ui/Icon";
import { IncidentLifecycleStatus } from "@/lib/api/incidents";

export interface StatusTransitionModalProps {
  isOpen: boolean;
  targetStatus: IncidentLifecycleStatus | null;
  title: string;
  message: string;
  errorMessage?: string | null;
  isSubmitting: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
  triggerRef?: React.RefObject<HTMLElement | null>;
}

export function StatusTransitionModal({
  isOpen,
  targetStatus,
  title,
  message,
  errorMessage,
  isSubmitting,
  onConfirm,
  onCancel,
  triggerRef,
}: StatusTransitionModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const wasSubmittingRef = useRef<boolean>(isSubmitting);

  // Modal open/close lifecycle and trigger focus restoration:
  // Captured when modal opens; restored ONLY when the modal closes or unmounts.
  // Not triggered during submission or API failure.
  useEffect(() => {
    if (!isOpen) return;

    // Capture the trigger element when the modal opens
    const triggerEl = triggerRef?.current ?? null;

    // Set initial focus inside the dialog
    const timer = setTimeout(() => {
      confirmButtonRef.current?.focus();
    }, 50);

    return () => {
      clearTimeout(timer);
      // Restore focus to trigger element only when the modal closes or unmounts
      triggerEl?.focus();
    };
  }, [isOpen, triggerRef]);

  // When submission finishes while the modal remains open (e.g. rejection or error),
  // restore focus to an appropriate enabled control inside the dialog.
  useEffect(() => {
    if (wasSubmittingRef.current && !isSubmitting && isOpen) {
      const timer = setTimeout(() => {
        if (confirmButtonRef.current && !confirmButtonRef.current.disabled) {
          confirmButtonRef.current.focus();
        } else if (cancelButtonRef.current && !cancelButtonRef.current.disabled) {
          cancelButtonRef.current.focus();
        } else {
          dialogRef.current?.focus();
        }
      }, 50);
      return () => clearTimeout(timer);
    }
    wasSubmittingRef.current = isSubmitting;
  }, [isSubmitting, isOpen]);

  // Keyboard navigation & focus trapping while modal is open:
  // - Escape is disabled during submission
  // - Tab and Shift+Tab wrap focus strictly within the dialog
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Escape key: dismiss modal only when not actively submitting
      if (e.key === "Escape") {
        if (!isSubmitting) {
          e.preventDefault();
          onCancel();
        }
        return;
      }

      // Tab key focus trap
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );

        if (focusable.length === 0) {
          e.preventDefault();
          dialogRef.current.focus();
          return;
        }

        const firstElement = focusable[0];
        const lastElement = focusable[focusable.length - 1];

        if (e.shiftKey) {
          if (
            document.activeElement === firstElement ||
            !dialogRef.current.contains(document.activeElement)
          ) {
            e.preventDefault();
            lastElement.focus();
          }
        } else {
          if (
            document.activeElement === lastElement ||
            !dialogRef.current.contains(document.activeElement)
          ) {
            e.preventDefault();
            firstElement.focus();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, isSubmitting, onCancel]);

  if (!isOpen || !targetStatus) {
    return null;
  }

  const isTerminal =
    targetStatus === "RESOLVED" || targetStatus === "FALSE_POSITIVE";
  const isFalsePositive = targetStatus === "FALSE_POSITIVE";

  // Configure action styling per target status
  let confirmButtonClass = "bg-[#1F5A43] hover:bg-[#184634] text-white";
  let confirmIcon = "check_circle";

  if (isFalsePositive) {
    confirmButtonClass = "bg-[#A34B4B] hover:bg-[#8B3F3F] text-white";
    confirmIcon = "cancel";
  } else if (targetStatus === "MITIGATED") {
    confirmButtonClass = "bg-[#9A7440] hover:bg-[#856335] text-white";
    confirmIcon = "shield";
  } else if (targetStatus === "INVESTIGATING") {
    confirmButtonClass = "bg-[#1F5A43] hover:bg-[#184634] text-white";
    confirmIcon = "search";
  }

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#171A18]/45"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="transition-modal-title"
        aria-describedby="transition-modal-description"
        tabIndex={-1}
        className="w-full max-w-md bg-white border border-[#D8D5CD] rounded-xl shadow-xl p-6 flex flex-col gap-4 text-[#171A18] focus:outline-none"
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          <div
            className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
              isFalsePositive
                ? "bg-[#FEECEB] text-[#A34B4B] border border-[#FCD3CE]"
                : targetStatus === "MITIGATED"
                ? "bg-[#FEF7E6] text-[#9A7440] border border-[#FDE8B3]"
                : "bg-[#EDF9ED] text-[#1F5A43] border border-[#C5ECC4]"
            }`}
          >
            <Icon name={confirmIcon} size={20} />
          </div>
          <div className="flex flex-col gap-1 min-w-0 flex-1">
            <h2
              id="transition-modal-title"
              className="text-[16px] font-semibold text-[#171A18] leading-tight"
            >
              {title}
            </h2>
            <div className="flex items-center gap-2 pt-0.5">
              <span className="text-[11px] font-medium text-[#5F6360] uppercase tracking-wider">
                Target Transition:
              </span>
              <span
                className={`font-mono text-[11px] px-1.5 py-0.5 rounded font-semibold border ${
                  isFalsePositive
                    ? "bg-[#FEECEB] text-[#A34B4B] border-[#FCD3CE]"
                    : targetStatus === "MITIGATED"
                    ? "bg-[#FEF7E6] text-[#9A7440] border-[#FDE8B3]"
                    : "bg-[#EDF9ED] text-[#1F5A43] border-[#C5ECC4]"
                }`}
              >
                {targetStatus}
              </span>
            </div>
          </div>
        </div>

        {/* Message */}
        <p
          id="transition-modal-description"
          className="text-[13px] text-[#5F6360] leading-relaxed"
        >
          {message}
        </p>

        {/* Error message banner if transition was rejected or failed */}
        {errorMessage && (
          <div
            role="alert"
            className="p-3 rounded-lg bg-[#FEECEB] border border-[#FCD3CE] text-[#A34B4B] text-[12px] flex items-start gap-2"
          >
            <Icon name="error" size={16} className="shrink-0 mt-0.5" />
            <span className="leading-relaxed">{errorMessage}</span>
          </div>
        )}

        {/* Terminal warning banner */}
        {isTerminal && (
          <div className="p-3 rounded-lg bg-[#FEF7E6] border border-[#FDE8B3] flex items-start gap-2.5 text-[#9A7440] text-[12px]">
            <Icon name="warning" size={16} className="shrink-0 mt-0.5" />
            <span>
              This is a terminal lifecycle transition. Once marked as{" "}
              {targetStatus}, no further status modifications can be made on
              this incident record.
            </span>
          </div>
        )}

        {/* Action Controls */}
        <div className="flex items-center justify-end gap-2.5 pt-2 border-t border-[#D8D5CD]">
          <button
            ref={cancelButtonRef}
            type="button"
            disabled={isSubmitting}
            onClick={onCancel}
            className="px-3.5 py-2 text-[13px] font-medium rounded-lg border border-[#D8D5CD] bg-[#F5F3EE] hover:bg-[#EAE7DF] text-[#171A18] transition-colors disabled:opacity-50 cursor-pointer"
          >
            Cancel
          </button>
          <button
            ref={confirmButtonRef}
            type="button"
            disabled={isSubmitting}
            onClick={onConfirm}
            className={`inline-flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-lg transition-colors shadow-xs disabled:opacity-50 cursor-pointer ${confirmButtonClass}`}
          >
            {isSubmitting ? (
              <>
                <Icon name="sync" size={16} className="animate-spin" />
                <span>Updating...</span>
              </>
            ) : (
              <>
                <Icon name={confirmIcon} size={16} />
                <span>Confirm Transition</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
