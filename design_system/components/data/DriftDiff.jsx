import React from "react";

const Col = ({ title, tone, children }) => (
  <div style={{ flex: 1, minWidth: 0 }}>
    <div style={{ font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: tone || "var(--text-muted)", marginBottom: 8 }}>{title}</div>
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{children}</div>
  </div>
);

const Line = ({ children, strike, tone }) => (
  <div style={{ font: "var(--type-mono)", color: tone || "var(--text-body)", textDecoration: strike ? "line-through" : "none", opacity: strike ? 0.6 : 1, padding: "3px 8px", borderRadius: "var(--radius-xs)", background: "var(--surface-sunken)" }}>{children}</div>
);

/**
 * DriftDiff — desired vs actual reconcile view for the Fleet page. Three
 * columns (Desired · Actual · Drift); drift rows are highlighted amber so the
 * operator sees exactly what reconcile would change before applying.
 */
export function DriftDiff({ desired = [], actual = [], drift = [], style, ...rest }) {
  return (
    <div
      {...rest}
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        padding: "16px 18px",
        display: "flex",
        gap: 20,
        ...style,
      }}
    >
      <Col title="Desired">
        {desired.map((d, i) => <Line key={i}>{d}</Line>)}
      </Col>
      <Col title="Actual">
        {actual.map((a, i) => <Line key={i} tone={a.startsWith("!") ? "var(--status-warn-fg)" : undefined}>{a.replace(/^!/, "")}</Line>)}
      </Col>
      <Col title="Drift" tone={drift.length ? "var(--status-warn-fg)" : "var(--status-ok-fg)"}>
        {drift.length
          ? drift.map((d, i) => (
              <div key={i} style={{ font: "var(--type-mono)", color: "var(--status-warn-fg)", padding: "3px 8px", borderRadius: "var(--radius-xs)", background: "var(--amber-50)", border: "1px solid var(--status-warn-border)" }}>{d}</div>
            ))
          : <Line tone="var(--status-ok-fg)">no drift · in sync</Line>}
      </Col>
    </div>
  );
}
