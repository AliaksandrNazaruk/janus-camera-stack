import React from "react";
import { statusFamily } from "../core/StatusBadge";

/**
 * EventTimeline — chronological FDIR / audit / activity feed. Each entry has a
 * mono timestamp, a status-colored node, a message, and optional actor/target
 * mono metadata. Used in Diagnostics, the Command-Center event list, and node
 * / stream Activity tabs.
 */
export function EventTimeline({ events = [], dense = false, style, ...rest }) {
  return (
    <div {...rest} style={{ display: "flex", flexDirection: "column", ...style }}>
      {events.map((e, i) => {
        const fam = e.family || statusFamily(e.result || e.level || "info");
        const last = i === events.length - 1;
        return (
          <div key={e.id || i} style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
            {/* rail */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 10, flex: "none" }}>
              <span style={{ width: 9, height: 9, borderRadius: "var(--radius-pill)", background: `var(--status-${fam}-solid)`, marginTop: dense ? 6 : 8, flex: "none", boxShadow: "0 0 0 3px var(--surface-card)" }} />
              {!last && <span style={{ width: 2, flex: 1, background: "var(--slate-200)" }} />}
            </div>
            {/* body */}
            <div style={{ paddingBottom: last ? 0 : dense ? 10 : 14, flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <span style={{ font: "var(--type-mono)", color: "var(--text-faint)" }}>{e.time}</span>
                {e.target && <span style={{ font: "var(--type-mono-strong)", color: `var(--status-${fam}-fg)` }}>{e.target}</span>}
                <span style={{ font: "var(--weight-regular) var(--text-base)/1.4 var(--font-sans)", color: "var(--text-body)" }}>{e.message}</span>
              </div>
              {(e.actor || e.action) && (
                <span style={{ font: "var(--type-mono)", color: "var(--text-faint)", fontSize: "var(--text-2xs)" }}>
                  {e.action ? e.action : ""}{e.actor ? ` · by ${e.actor}` : ""}{e.reason ? ` · ${e.reason}` : ""}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
