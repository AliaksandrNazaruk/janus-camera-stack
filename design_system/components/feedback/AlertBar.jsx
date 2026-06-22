import React from "react";
import { ActionButton } from "../core/ActionButton";

const SEV = {
  critical: { bg: "var(--alert-critical-bg)", fg: "var(--alert-critical-fg)", accent: "var(--alert-critical-accent)", icon: "octagon-alert" },
  warning: { bg: "var(--alert-warning-bg)", fg: "var(--alert-warning-fg)", accent: "var(--alert-warning-accent)", icon: "triangle-alert" },
  info: { bg: "var(--alert-info-bg)", fg: "var(--alert-info-fg)", accent: "var(--alert-info-accent)", icon: "info" },
};

/**
 * AlertBar — the global alert strip pinned under the topbar. Shows the highest
 * open severity, a count, the lead message, and an optional action. Alerts are
 * grouped by severity (critical / warning / info); pass the worst one here.
 */
export function AlertBar({ severity = "info", message, count, actionLabel, onAction, onDismiss, style, ...rest }) {
  const s = SEV[severity] || SEV.info;
  return (
    <div
      {...rest}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        minHeight: "var(--alertbar-h)",
        padding: "0 14px",
        background: s.bg,
        borderBottom: `1px solid ${s.accent}`,
        boxShadow: `inset var(--border-accent) 0 0 0 ${s.accent}`,
        ...style,
      }}
    >
      <i data-lucide={s.icon} style={{ width: 16, height: 16, color: s.accent, flex: "none" }} />
      {count != null && (
        <span style={{ font: "var(--weight-bold) var(--text-2xs)/1 var(--font-sans)", color: "var(--white)", background: s.accent, padding: "2px 7px", borderRadius: "var(--radius-pill)" }}>{count}</span>
      )}
      <span style={{ font: "var(--weight-medium) var(--text-base)/1.3 var(--font-sans)", color: s.fg, flex: 1 }}>{message}</span>
      {actionLabel && <ActionButton size="xs" variant="ghost" onClick={onAction} style={{ color: s.fg }}>{actionLabel}</ActionButton>}
      {onDismiss && <ActionButton size="xs" variant="ghost" icon="x" onClick={onDismiss} style={{ color: s.fg }} aria-label="Dismiss" />}
    </div>
  );
}
