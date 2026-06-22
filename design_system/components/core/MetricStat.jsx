import React from "react";

/**
 * MetricStat — a single Command-Center counter (Nodes Online, Streams Live,
 * FDIR Events, Open Alerts). Big mono numeral over an uppercase label, with an
 * optional status accent and trend/subtext line.
 */
export function MetricStat({
  label,
  value,
  total,
  family = "idle",
  hint,
  icon,
  style,
  ...rest
}) {
  return (
    <div
      {...rest}
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        position: "relative",
        overflow: "hidden",
        ...style,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: 0, top: 0, bottom: 0,
          width: "var(--border-accent)",
          background: `var(--status-${family}-solid)`,
        }}
      />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span
          style={{
            font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)",
            textTransform: "uppercase",
            letterSpacing: "var(--tracking-label)",
            color: "var(--text-muted)",
          }}
        >
          {label}
        </span>
        {icon && (
          <i data-lucide={icon} style={{ width: 15, height: 15, color: "var(--text-faint)" }} />
        )}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
        <span
          style={{
            font: "var(--weight-bold) var(--text-3xl)/1 var(--font-mono)",
            letterSpacing: "var(--tracking-tight)",
            color: `var(--status-${family}-fg)`,
          }}
        >
          {value}
        </span>
        {total != null && (
          <span
            style={{
              font: "var(--weight-medium) var(--text-lg)/1 var(--font-mono)",
              color: "var(--text-faint)",
            }}
          >
            /{total}
          </span>
        )}
      </div>
      {hint && (
        <span style={{ font: "var(--type-body)", fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
          {hint}
        </span>
      )}
    </div>
  );
}
