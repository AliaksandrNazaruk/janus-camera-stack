import React from "react";
import { ActionButton } from "../core/ActionButton";

/**
 * ConfirmDialog — modal for Class C (service-impacting) and Class D
 * (destructive) actions. Class C shows an impact list; Class D additionally
 * requires the operator to type an exact phrase before the confirm button
 * unlocks, and lays out what will be removed vs kept plus the rollback path.
 */
export function ConfirmDialog({
  open,
  title,
  destructive = false,
  message,
  impact = [],
  willRemove = [],
  willKeep = [],
  rollback,
  confirmPhrase,
  confirmLabel = "Confirm",
  onConfirm,
  onClose,
}) {
  const [typed, setTyped] = React.useState("");
  React.useEffect(() => { if (open) setTyped(""); }, [open]);
  if (!open) return null;
  const locked = confirmPhrase && typed.trim() !== confirmPhrase;
  const accent = destructive ? "bad" : "warn";
  const List = ({ items, icon, color }) => (
    <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 5 }}>
      {items.map((it, i) => (
        <li key={i} style={{ display: "flex", gap: 7, alignItems: "baseline", font: "var(--type-mono)", color: color || "var(--text-body)" }}>
          <i data-lucide={icon} style={{ width: 13, height: 13, flex: "none", position: "relative", top: 2 }} />{it}
        </li>
      ))}
    </ul>
  );
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 220, display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "9vh" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "var(--surface-overlay)" }} />
      <div style={{ position: "relative", width: 480, maxWidth: "92vw", background: "var(--surface-card)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-overlay)", overflow: "hidden", animation: "gc-pop 160ms ease" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 18px", borderBottom: "1px solid var(--border-subtle)" }}>
          <span style={{ width: 30, height: 30, borderRadius: "var(--radius-md)", background: `var(--status-${accent}-bg)`, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
            <i data-lucide={destructive ? "octagon-alert" : "triangle-alert"} style={{ width: 17, height: 17, color: `var(--status-${accent}-solid)` }} />
          </span>
          <span style={{ font: "var(--type-section)", color: "var(--text-strong)" }}>{title}</span>
        </div>
        <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14 }}>
          {message && <p style={{ margin: 0, font: "var(--type-body)", color: "var(--text-body)" }}>{message}</p>}
          {impact.length > 0 && (
            <div><div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 7 }}>Impact</div><List items={impact} icon="dot" /></div>
          )}
          {willRemove.length > 0 && (
            <div><div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--status-bad-fg)", marginBottom: 7 }}>Will be removed</div><List items={willRemove} icon="minus" color="var(--status-bad-fg)" /></div>
          )}
          {willKeep.length > 0 && (
            <div><div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--status-ok-fg)", marginBottom: 7 }}>Will stay</div><List items={willKeep} icon="check" color="var(--status-ok-fg)" /></div>
          )}
          {rollback && (
            <div style={{ font: "var(--type-body)", color: "var(--text-muted)" }}><b style={{ color: "var(--text-body)" }}>Rollback:</b> {rollback}</div>
          )}
          {confirmPhrase && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={{ font: "var(--type-body)", color: "var(--text-body)" }}>Type <code style={{ font: "var(--type-mono-strong)", color: "var(--status-bad-fg)", background: "var(--surface-sunken)", padding: "1px 6px", borderRadius: "var(--radius-xs)" }}>{confirmPhrase}</code> to confirm</span>
              <input
                autoFocus
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={confirmPhrase}
                style={{ font: "var(--type-mono)", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: `1px solid ${locked ? "var(--border-default)" : "var(--status-ok-solid)"}`, outline: "none", color: "var(--text-strong)" }}
              />
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, padding: "14px 18px", borderTop: "1px solid var(--border-subtle)", justifyContent: "flex-end" }}>
          <ActionButton variant="ghost" onClick={onClose}>Cancel</ActionButton>
          <ActionButton variant={destructive ? "danger-solid" : "warning"} disabled={locked} onClick={onConfirm}>{confirmLabel}</ActionButton>
        </div>
      </div>
    </div>
  );
}
