import React from "react";
import { StatusBadge, statusFamily } from "../core/StatusBadge";

/**
 * HealthCard — the Command-Center "System Status" strip. A title plus a row of
 * service health pills (Gateway · Janus · Cloudflare · FDIR · Firewall …).
 * The card's left accent reflects the worst service state so a single glance
 * answers "is everything healthy?".
 */
export function HealthCard({ title = "System Status", services = [], style, ...rest }) {
  const order = { bad: 0, warn: 1, busy: 2, idle: 3, ok: 4 };
  const worst = services.reduce((acc, s) => {
    const f = statusFamily(s.status);
    return order[f] < order[acc] ? f : acc;
  }, "ok");
  return (
    <div
      {...rest}
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderLeft: `var(--border-accent) solid var(--status-${worst}-solid)`,
        borderRadius: "var(--radius-lg)",
        padding: "13px 16px",
        display: "flex",
        alignItems: "center",
        gap: 16,
        flexWrap: "wrap",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <i data-lucide={worst === "ok" ? "shield-check" : "shield-alert"} style={{ width: 18, height: 18, color: `var(--status-${worst}-solid)` }} />
        <span style={{ font: "var(--type-card-title)", color: "var(--text-strong)" }}>{title}</span>
      </div>
      <div style={{ width: 1, alignSelf: "stretch", background: "var(--border-subtle)" }} />
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
        {services.map((s) => (
          <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ font: "var(--weight-medium) var(--text-sm)/1 var(--font-sans)", color: "var(--text-muted)" }}>{s.name}</span>
            <StatusBadge state={s.status} label={s.label} size="sm" />
          </div>
        ))}
      </div>
    </div>
  );
}
