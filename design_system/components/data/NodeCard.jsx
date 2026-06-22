import React from "react";
import { StatusBadge, statusFamily } from "../core/StatusBadge";
import { ActionButton } from "../core/ActionButton";

const HealthItem = ({ label, value, family }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
    <span style={{ font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)" }}>{label}</span>
    {family
      ? <StatusBadge family={family} label={value} size="sm" />
      : <span style={{ font: "var(--type-mono)", color: "var(--text-body)" }}>{value}</span>}
  </div>
);

/**
 * NodeCard — a physical camera node (local gateway `.10` or remote `.55`).
 * Identical layout for both; only the left accent differs (blue = local,
 * slate = remote). Shows the health grid, per-sensor stream summary, the safe
 * + operational action row, and a separated Danger Zone toggle.
 */
export function NodeCard({
  nodeId,
  host,
  role = "remote_producer",
  model,
  serial,
  status = "online",
  local = false,
  health = {},
  streams = [],
  onCheck,
  onProvision,
  onMaintenance,
  onRotate,
  onOpenStreams,
  onRemove,
  style,
  ...rest
}) {
  const [danger, setDanger] = React.useState(false);
  return (
    <div
      {...rest}
      style={{
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderLeft: `var(--border-accent) solid ${local ? "var(--blue-600)" : "var(--slate-400)"}`,
        borderRadius: "var(--radius-lg)",
        padding: "16px 18px",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        ...style,
      }}
    >
      {/* Head */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 180 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span style={{ font: "var(--weight-bold) var(--text-xl)/1 var(--font-mono)", color: "var(--text-strong)" }}>{nodeId}</span>
            <StatusBadge state={status} pulse={status === "provisioning" || status === "recovering"} />
            <span style={{ font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: local ? "var(--blue-700)" : "var(--text-muted)", background: local ? "var(--blue-50)" : "var(--surface-sunken)", padding: "3px 7px", borderRadius: "var(--radius-xs)" }}>{local ? "local" : "remote"}</span>
          </div>
          <span style={{ font: "var(--type-mono)", color: "var(--text-muted)" }}>
            {host} · {role.replace(/_/g, " ")}{model ? ` · ${model}` : ""}{serial ? ` · serial ${serial}` : ""}
          </span>
        </div>
        <ActionButton size="sm" variant="ghost" icon="stethoscope" onClick={onCheck}>Check</ActionButton>
      </div>

      {/* Health grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))", gap: 12, padding: "12px 0", borderTop: "1px solid var(--slate-100)", borderBottom: "1px solid var(--slate-100)" }}>
        <HealthItem label="Agent" value={health.agent || "online"} family={statusFamily(health.agent || "online")} />
        <HealthItem label="Camera" value={health.camera || "present"} family={statusFamily(health.camera || "present")} />
        <HealthItem label="Last seen" value={health.lastSeen || "8s ago"} />
        <HealthItem label="Provision" value={health.provision || "ready"} family={statusFamily(health.provision || "ready")} />
        <HealthItem label="Maintenance" value={health.maintenance || "off"} family={health.maintenance === "on" ? "busy" : "idle"} />
        <HealthItem label="Host key" value={health.hostKey || "pinned"} family={health.hostKey === "pinned" ? "ok" : "warn"} />
        <HealthItem label="Token" value={health.token || "present"} family={statusFamily(health.token === "present" ? "valid" : health.token)} />
      </div>

      {/* Streams */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {streams.map((s) => (
          <div key={s.sensor} style={{ display: "flex", alignItems: "center", gap: 10, font: "var(--type-mono)" }}>
            <span style={{ color: "var(--text-strong)", fontWeight: 500, width: 48 }}>{s.sensor}</span>
            <StatusBadge state={s.status} size="sm" />
            <span style={{ color: "var(--text-faint)", marginLeft: "auto" }}>mp {s.mp} · port {s.port} · rtp {s.rtpAge}</span>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <ActionButton size="sm" variant="primary" icon="layers" onClick={onOpenStreams}>Streams</ActionButton>
        <ActionButton size="sm" variant="default" icon="download" onClick={onProvision}>Provision</ActionButton>
        <ActionButton size="sm" variant="default" icon="wrench" onClick={onMaintenance}>Maintenance</ActionButton>
        <ActionButton size="sm" variant="default" icon="key-round" onClick={onRotate}>Rotate token</ActionButton>
        <ActionButton size="sm" variant="ghost" icon={danger ? "chevron-up" : "chevron-down"} style={{ marginLeft: "auto" }} onClick={() => setDanger((d) => !d)}>Danger Zone</ActionButton>
      </div>

      {danger && (
        <div style={{ background: "var(--red-50)", border: "1px solid var(--status-bad-border)", borderRadius: "var(--radius-md)", padding: "11px 13px", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--red-700)", marginRight: 4 }}>Danger Zone</span>
          <ActionButton size="xs" variant="danger" icon="trash-2" onClick={onRemove}>Remove node</ActionButton>
          <ActionButton size="xs" variant="danger" icon="power">Deprovision</ActionButton>
          <ActionButton size="xs" variant="danger" icon="key-square">Forget host key</ActionButton>
        </div>
      )}
    </div>
  );
}
