import React from "react";
import { StatusBadge } from "../core/StatusBadge";
import { ActionButton } from "../core/ActionButton";

const Mono = ({ children, muted, style }) => (
  <span
    style={{
      font: "var(--type-mono)",
      color: muted ? "var(--text-faint)" : "var(--text-body)",
      ...style,
    }}
  >
    {children}
  </span>
);

/* rtp_age coloring: fresh < 1s green-ish, 1-5s amber, > 5s red. Returns a
 * status family for the value text. */
function ageFamily(ms) {
  if (ms == null) return "idle";
  if (ms < 1000) return "ok";
  if (ms < 5000) return "warn";
  return "bad";
}
function fmtAge(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + "s";
}

/**
 * StreamRow — one row of the Streams table. Local and remote streams render
 * identically (a core requirement). Shows binding id, status, rtp_age, Janus
 * mp/port, FDIR state, last error, and the primary action set.
 */
export function StreamRow({
  binding,         // "cam55:color"
  node,
  sensor,
  status = "online",
  rtpAgeMs,
  mountpoint,
  rtpPort,
  fdir = "enabled",
  lastError,
  onOpen,
  onRestart,
  onStop,
  onDiagnose,
  selected = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const cell = { padding: "9px 12px", verticalAlign: "middle", borderBottom: "1px solid var(--slate-100)" };
  return (
    <tr
      {...rest}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ background: selected ? "var(--blue-50)" : hover ? "var(--surface-hover)" : "transparent", ...style }}
    >
      <td style={{ ...cell, paddingLeft: 14 }}>
        <span style={{ font: "var(--type-mono-strong)", color: "var(--text-strong)" }}>{binding || `${node}:${sensor}`}</span>
      </td>
      <td style={cell}><Mono>{node}</Mono></td>
      <td style={cell}><Mono muted>{sensor}</Mono></td>
      <td style={cell}><StatusBadge state={status} size="sm" /></td>
      <td style={cell}>
        <span style={{ font: "var(--type-mono-strong)", color: `var(--status-${ageFamily(rtpAgeMs)}-fg)` }}>{fmtAge(rtpAgeMs)}</span>
      </td>
      <td style={cell}><Mono muted>{mountpoint ?? "—"}</Mono></td>
      <td style={cell}><Mono muted>{rtpPort ?? "—"}</Mono></td>
      <td style={cell}>
        <StatusBadge family={fdir === "enabled" ? "ok" : fdir === "suppressed" ? "busy" : "idle"} label={fdir === "enabled" ? "on" : fdir === "suppressed" ? "supp" : "off"} size="sm" dot={false} />
      </td>
      <td style={cell}>
        {lastError ? <Mono style={{ color: "var(--status-bad-fg)" }}>{lastError}</Mono> : <Mono muted>—</Mono>}
      </td>
      <td style={{ ...cell, paddingRight: 14 }}>
        <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
          <ActionButton size="xs" variant="ghost" icon="external-link" onClick={onOpen} aria-label="Open viewer" />
          <ActionButton size="xs" variant="default" icon="rotate-cw" onClick={onRestart}>Restart</ActionButton>
          {status === "online" || status === "stale" || status === "degraded"
            ? <ActionButton size="xs" variant="warning" onClick={onStop}>Stop</ActionButton>
            : <ActionButton size="xs" variant="ghost" icon="stethoscope" onClick={onDiagnose}>Diagnose</ActionButton>}
        </div>
      </td>
    </tr>
  );
}
