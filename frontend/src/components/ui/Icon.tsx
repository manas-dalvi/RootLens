import React from "react";

interface IconProps {
  name: string;
  className?: string;
  size?: number;
}

export function Icon({ name, className = "", size }: IconProps) {
  return (
    <span
      className={`material-symbols-outlined select-none shrink-0 inline-flex items-center justify-center ${className}`}
      style={size ? { fontSize: `${size}px`, width: `${size}px`, height: `${size}px` } : undefined}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}
