import React from "react";
import { StatusBadge } from "../core/StatusBadge";
import { ActionButton } from "../core/ActionButton";

/**
 * ViewerTile — one tile of the Viewer Wall. A 16:9 video surface with a status
 * overlay (binding id · state · rtp_age), an optional last-FDIR-event badge,
 * a pin toggle, and a per-tile quick-action footer. Use a real <video> via the
 * `media` slot; otherwise a placeholder frame renders.
 */
export function ViewerTile({
  binding,
  status = "online",
  rtpAge,
  fdirEvent,
  pinned = false,
  media,
  onRestart,
  onDiagnose,
  onPin,
  onFullscreen,
  style,
  ...rest
}) {
  return (
    <div
      {...rest}
      style={{
        background: "var(--slate-950)",
        border: `1px solid ${pinned ? "var(--blue-600)" : "var(--border-default)"}`,
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
    >
      {/* header overlay */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "linear-gradient(180deg, rgba(2,6,23,0.85), rgba(2,6,23,0))", position: "relative", zIndex: 2 }}>
        <span style={{ font: "var(--type-mono-strong)", color: "var(--white)" }}>{binding}</span>
        <StatusBadge state={status} size="sm" />
        {rtpAge != null && <span style={{ font: "var(--type-mono)", color: "rgba(255,255,255,0.7)" }}>rtp {rtpAge}</span>}
        <ActionButton size="xs" variant="ghost" icon={pinned ? "pin" : "pin-off"} onClick={onPin} style={{ marginLeft: "auto", color: pinned ? "var(--blue-400)" : "rgba(255,255,255,0.6)" }} aria-label="Pin" />
      </div>
      {/* video surface */}
      <div style={{ position: "relative", aspectRatio: "16 / 9", background: "#000", marginTop: -40, display: "flex", alignItems: "center", justifyContent: "center" }}>
        {media || (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, color: "rgba(255,255,255,0.25)" }}>
            <i data-lucide="video" style={{ width: 30, height: 30 }} />
            <span style={{ font: "var(--type-mono)", fontSize: "var(--text-2xs)" }}>{binding}</span>
          </div>
        )}
        {fdirEvent && (
          <span style={{ position: "absolute", left: 10, bottom: 10, font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", color: "var(--white)", background: "var(--status-busy-solid)", padding: "3px 7px", borderRadius: "var(--radius-xs)", display: "inline-flex", alignItems: "center", gap: 4 }}>
            <i data-lucide="activity" style={{ width: 11, height: 11 }} />FDIR {fdirEvent}
          </span>
        )}
      </div>
      {/* footer actions */}
      <div style={{ display: "flex", gap: 6, padding: "8px 10px", background: "var(--slate-900)", borderTop: "1px solid var(--border-chrome)" }}>
        <ActionButton size="xs" variant="default" icon="rotate-cw" onClick={onRestart} style={{ background: "var(--slate-800)", borderColor: "var(--slate-700)", color: "var(--slate-100)" }}>Restart</ActionButton>
        <ActionButton size="xs" variant="ghost" icon="stethoscope" onClick={onDiagnose} style={{ color: "var(--slate-300)" }}>Diag</ActionButton>
        <ActionButton size="xs" variant="ghost" icon="maximize-2" onClick={onFullscreen} style={{ marginLeft: "auto", color: "var(--slate-300)" }} aria-label="Fullscreen" />
      </div>
    </div>
  );
}
