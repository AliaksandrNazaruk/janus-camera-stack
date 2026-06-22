import React from "react";
import { ActionButton } from "../core/ActionButton";
import { StatusBadge } from "../core/StatusBadge";

const StepRow = ({ label, state }) => {
  const fam = state === "ok" ? "ok" : state === "active" ? "busy" : state === "failed" ? "bad" : "idle";
  const icon = state === "ok" ? "check" : state === "failed" ? "x" : state === "active" ? "loader-circle" : "circle";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0" }}>
      <i data-lucide={icon} style={{ width: 15, height: 15, color: `var(--status-${fam}-solid)`, animation: state === "active" ? "gc-spin 0.8s linear infinite" : "none" }} />
      <span style={{ font: "var(--type-body)", color: state === "pending" ? "var(--text-faint)" : "var(--text-body)" }}>{label}</span>
    </div>
  );
};

/**
 * OperationDrawer — every mutation opens this right-side drawer. Pre-execution
 * it states the action, its impact, the FDIR consequence and expected
 * duration; on confirm it streams step progress. Embodies the spec's
 * dry-run → impact → confirm → verify flow for Class B/C operations.
 */
export function OperationDrawer({
  open,
  title,
  target,
  impactClass = "B",
  impact = [],
  fdirNote,
  duration,
  steps,
  running = false,
  result,
  confirmLabel = "Confirm",
  onConfirm,
  onClose,
}) {
  if (!open) return null;
  const classColor = { A: "ok", B: "busy", C: "warn", D: "bad" }[impactClass] || "busy";
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 200, display: "flex", justifyContent: "flex-end" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "var(--surface-overlay)", backdropFilter: "blur(1px)" }} />
      <aside style={{ position: "relative", width: "var(--drawer-w)", maxWidth: "92vw", height: "100%", background: "var(--surface-card)", boxShadow: "var(--shadow-overlay)", display: "flex", flexDirection: "column", animation: "gc-slidein 180ms ease" }}>
        {/* header */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "16px 18px", borderBottom: "1px solid var(--border-subtle)" }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ font: "var(--type-section)", color: "var(--text-strong)" }}>{title}</span>
              <span style={{ font: "var(--weight-bold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: `var(--status-${classColor}-fg)`, background: `var(--status-${classColor}-bg)`, border: `1px solid var(--status-${classColor}-border)`, padding: "3px 7px", borderRadius: "var(--radius-xs)" }}>Class {impactClass}</span>
            </div>
            {target && <span style={{ font: "var(--type-mono)", color: "var(--text-muted)" }}>{target}</span>}
          </div>
          <ActionButton size="xs" variant="ghost" icon="x" onClick={onClose} aria-label="Close" />
        </div>

        {/* body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 18px", display: "flex", flexDirection: "column", gap: 16 }}>
          {!result && (
            <>
              {impact.length > 0 && (
                <section>
                  <div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 8 }}>Impact</div>
                  <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 5 }}>
                    {impact.map((it, i) => <li key={i} style={{ font: "var(--type-body)", color: "var(--text-body)" }}>{it}</li>)}
                  </ul>
                </section>
              )}
              <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
                {fdirNote && (
                  <div><div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 6 }}>FDIR</div><StatusBadge family={fdirNote.includes("disabled") ? "idle" : "ok"} label={fdirNote} size="sm" /></div>
                )}
                {duration && (
                  <div><div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 6 }}>Expected duration</div><span style={{ font: "var(--type-mono-strong)", color: "var(--text-strong)" }}>{duration}</span></div>
                )}
              </div>
            </>
          )}

          {steps && (
            <section>
              <div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 4 }}>Progress</div>
              <div>{steps.map((s, i) => <StepRow key={i} label={s.label} state={s.state} />)}</div>
            </section>
          )}

          {result && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 13px", borderRadius: "var(--radius-md)", background: result === "ok" ? "var(--status-ok-bg)" : "var(--status-bad-bg)", color: result === "ok" ? "var(--status-ok-fg)" : "var(--status-bad-fg)", font: "var(--weight-semibold) var(--text-md)/1 var(--font-sans)" }}>
              <i data-lucide={result === "ok" ? "circle-check-big" : "circle-x"} style={{ width: 18, height: 18 }} />
              {result === "ok" ? "Operation completed" : "Operation failed"}
            </div>
          )}
        </div>

        {/* footer */}
        <div style={{ display: "flex", gap: 8, padding: "14px 18px", borderTop: "1px solid var(--border-subtle)", justifyContent: "flex-end" }}>
          {result
            ? <ActionButton variant="primary" onClick={onClose}>Done</ActionButton>
            : <>
                <ActionButton variant="ghost" onClick={onClose}>Cancel</ActionButton>
                <ActionButton variant={impactClass === "C" ? "warning" : "primary"} busy={running} onClick={onConfirm}>{confirmLabel}</ActionButton>
              </>}
        </div>
      </aside>
    </div>
  );
}
