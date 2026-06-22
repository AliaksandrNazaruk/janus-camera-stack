import React from "react";
import { statusFamily } from "../core/StatusBadge";

/**
 * DiagnosticsPanel — a titled block of key/value diagnostic rows (the Agent /
 * Camera / Services / Data-plane / Control-plane sections of Diagnostics).
 * Values render in mono; pass `status` on a row to color it as a state.
 */
export function DiagnosticsPanel({ title, icon, rows = [], style, ...rest }) {
  return (
    <div
      {...rest}
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", background: "var(--surface-sunken)" }}>
        {icon && <i data-lucide={icon} style={{ width: 15, height: 15, color: "var(--text-muted)" }} />}
        <span style={{ font: "var(--type-label)", fontSize: "var(--text-xs)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" }}>{title}</span>
      </div>
      <div style={{ padding: "6px 14px" }}>
        {rows.map((r, i) => {
          const fam = r.status ? statusFamily(r.status) : null;
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 0", borderBottom: i < rows.length - 1 ? "1px solid var(--slate-100)" : "none" }}>
              <span style={{ font: "var(--type-mono)", color: "var(--text-muted)", width: 150, flex: "none" }}>{r.key}</span>
              {fam
                ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, font: "var(--type-mono-strong)", color: `var(--status-${fam}-fg)` }}>
                    <span style={{ width: 7, height: 7, borderRadius: "var(--radius-pill)", background: `var(--status-${fam}-solid)` }} />{r.value}
                  </span>
                : <span style={{ font: "var(--type-mono-strong)", color: "var(--text-strong)" }}>{r.value}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
