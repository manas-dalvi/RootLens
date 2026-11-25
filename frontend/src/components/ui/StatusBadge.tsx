import React from "react";

export type StatusVariant = "critical" | "warning" | "healthy" | "info" | "neutral";

interface StatusBadgeProps {
  label: string;
  variant?: StatusVariant;
  dot?: boolean;
  className?: string;
  mono?: boolean;
}

const variantStyles: Record<StatusVariant, { text: string; bg: string; border: string; dot: string }> = {
  critical: {
    text: "text-[#E63316]",
    bg: "bg-[#FEECEB]",
    border: "border-[#FCD3CE]",
    dot: "bg-[#E63316]",
  },
  warning: {
    text: "text-[#B77900]",
    bg: "bg-[#FEF7E6]",
    border: "border-[#FDE8B3]",
    dot: "bg-[#B77900]",
  },
  healthy: {
    text: "text-[#2E7D32]",
    bg: "bg-[#EDF9ED]",
    border: "border-[#C5ECC4]",
    dot: "bg-[#4CAF50]",
  },
  info: {
    text: "text-[#4D80FC]",
    bg: "bg-[#EEF3FF]",
    border: "border-[#BFDBFE]",
    dot: "bg-[#4D80FC]",
  },
  neutral: {
    text: "text-[#606366]",
    bg: "bg-[#F5F6F9]",
    border: "border-[#EDEFF3]",
    dot: "bg-[#9B9FA3]",
  },
};

export function StatusBadge({
  label,
  variant = "neutral",
  dot = false,
  className = "",
  mono = true,
}: StatusBadgeProps) {
  const styles = variantStyles[variant];

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-semibold border ${styles.bg} ${styles.text} ${styles.border} ${
        mono ? "font-mono" : ""
      } ${className}`}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${styles.dot}`} />}
      {label}
    </span>
  );
}
