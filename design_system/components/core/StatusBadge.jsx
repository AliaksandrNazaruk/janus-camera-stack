import React from "react";

/* Canonical mapping: every node / stream / FDIR / firewall state string the
 * gateway emits resolves to exactly one of five families. Unknown strings
 * fall back to "idle" so the UI never renders an uncolored status. */
const FAMILY_BY_STATE = {
  // ok · green
  online: "ok", synced: "ok", healthy: "ok", ready: "ok", active: "ok",
  enabled: "ok", present: "ok", valid: "ok", reachable: "ok", pinned: "ok",
  ok: "ok", connected: "ok", running: "ok",
  // warn · amber
  degraded: "warn", waiting: "warn", waiting_for_rtp: "warn", warning: "warn",
  drift: "warn", pending: "warn", rotated: "warn", unsigned: "warn",
  provision_required: "warn",
  // bad · red
  failed: "bad", stale: "bad", critical: "bad", stopped: "bad",
  unreachable: "bad", blocked: "bad", apply_failed: "bad", error: "bad",
  missing: "bad", down: "bad",
  // idle · gray
  offline: "idle", disabled: "idle", unknown: "idle", configured_offline: "idle",
  off: "idle", unset: "idle", absent: "idle", idle: "idle", removed: "idle",
  // busy · blue
  provisioning: "busy", recovering: "busy", maintenance: "busy",
  host_key_pending: "busy", registered: "busy", removing: "busy",
  suppressed: "busy",
};

/** Resolve a raw state string to its status family. */
export function statusFamily(state) {
  if (!state) return "idle";
  return FAMILY_BY_STATE[String(state).toLowerCase()] || "idle";
}

const SIZES = {
  sm: { pad: "1px 7px", font: "var(--text-2xs)", dot: 6, gap: 4 },
  md: { pad: "2px 9px", font: "var(--text-xs)", dot: 8, gap: 5 },
  lg: { pad: "4px 12px", font: "var(--text-sm)", dot: 9, gap: 6 },
};

/**
 * StatusBadge — the single status primitive for the console. Pass a raw state
 * string (e.g. "waiting_for_rtp"); it auto-maps to a family color and shows a
 * dot + label. Override the family with `family` when you need a fixed color.
 */
export function StatusBadge({
  state,
  label,
  family,
  size = "md",
  dot = true,
  pulse = false,
  style,
  ...rest
}) {
  const fam = family || statusFamily(state);
  const s = SIZES[size] || SIZES.md;
  const text = label != null ? label : String(state || "unknown").replace(/_/g, " ");
  return (
    <span
      {...rest}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: s.gap,
        padding: s.pad,
        borderRadius: "var(--radius-pill)",
        background: `var(--status-${fam}-bg)`,
        color: `var(--status-${fam}-fg)`,
        border: `1px solid var(--status-${fam}-border)`,
        font: `var(--weight-semibold) ${s.font}/1 var(--font-sans)`,
        whiteSpace: "nowrap",
        textTransform: "lowercase",
        letterSpacing: "0.01em",
        ...style,
      }}
    >
      {dot && (
        <span
          style={{
            width: s.dot,
            height: s.dot,
            borderRadius: "var(--radius-pill)",
            background: `var(--status-${fam}-solid)`,
            flex: "none",
            boxShadow: pulse ? `0 0 0 0 var(--status-${fam}-solid)` : "none",
            animation: pulse ? "gc-pulse 1.6s ease-out infinite" : "none",
          }}
        />
      )}
      {text}
    </span>
  );
}
