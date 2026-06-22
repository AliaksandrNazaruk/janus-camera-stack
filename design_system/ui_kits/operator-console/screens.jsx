// Operator Console screens — composes design-system components into the 7
// sections from the spec. Exposes window.SCREENS keyed by nav id.
const C = window.GatewayConsoleDesignSystem_64aa70;
const {
  StatusBadge, ActionButton, MetricStat, HealthCard, StreamRow, NodeCard,
  EventTimeline, DriftDiff, ViewerTile, DiagnosticsPanel,
} = C;

/* ── shared layout bits ─────────────────────────────────────────────────── */
function Panel({ title, action, children, pad = true, style }) {
  return (
    <section style={{ background: "var(--surface-card)", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-lg)", ...style }}>
      {title && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: "1px solid var(--border-subtle)" }}>
          <h2 style={{ margin: 0, font: "var(--type-card-title)", color: "var(--text-strong)" }}>{title}</h2>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>{action}</div>
        </div>
      )}
      <div style={{ padding: pad ? 16 : 0 }}>{children}</div>
    </section>
  );
}
// Row reimplemented here (not the compiled StreamRow) so we can add a per-stream
// Configure ⚙ action (rotation/resolution/fps/bitrate) — local streams only; remote
// node tuning is Phase B (see docs/design/STREAM_TUNING_CONSOLE.md).
const _stTd = { padding: "9px 12px", verticalAlign: "middle", font: "var(--type-mono)", color: "var(--text-body)" };
const _stFmtAge = (ms) => (ms == null ? "—" : ms < 1000 ? ms + "ms" : (Math.round(ms / 100) / 10) + "s");
const StreamsTable = ({ rows, onAction }) => (
  <table style={{ width: "100%", borderCollapse: "collapse" }}>
    <thead><tr>
      {["Stream", "Node", "Sensor", "Status", "RTP Age", "MP", "Port", "FDIR", "Last Error"].map((h) => (
        <th key={h} style={{ textAlign: "left", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "0 12px 9px", borderBottom: "1px solid var(--border-subtle)" }}>{h}</th>
      ))}
      <th style={{ textAlign: "right", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "0 14px 9px", borderBottom: "1px solid var(--border-subtle)" }}>Actions</th>
    </tr></thead>
    <tbody>
      {rows.map((s) => {
        const local = s.node === "cam10";
        // show Stop when the stream is actually live — online, OR fresh RTP even if
        // the stored status lags (e.g. a local stream whose desired_active is stale).
        const live = s.status === "online" || (typeof s.rtpAgeMs === "number" && s.rtpAgeMs < 2000);
        return (
          <tr key={s.binding} style={{ borderBottom: "1px solid var(--slate-100)" }}>
            <td style={{ ..._stTd, color: "var(--text-strong)" }}>{s.binding}</td>
            <td style={_stTd}>{s.node}</td>
            <td style={_stTd}>{s.sensor}</td>
            <td style={{ ..._stTd }}><StatusBadge state={s.status} size="sm" /></td>
            <td style={_stTd}>{_stFmtAge(s.rtpAgeMs)}</td>
            <td style={_stTd}>{s.mountpoint}</td>
            <td style={_stTd}>{s.rtpPort}</td>
            <td style={{ ..._stTd }}><StatusBadge family={s.fdir === "enabled" ? "ok" : "idle"} label={s.fdir} size="sm" dot={false} /></td>
            <td style={{ ..._stTd, color: "var(--text-muted)" }}>{s.lastError || "—"}</td>
            <td style={{ padding: "7px 14px", textAlign: "right", whiteSpace: "nowrap" }}>
              <ActionButton size="xs" variant="ghost" icon="external-link" onClick={() => onAction("open", s)} aria-label="Open viewer" />
              <ActionButton size="xs" variant="default" icon="rotate-cw" onClick={() => onAction("restart", s)}>Restart</ActionButton>
              {live && <ActionButton size="xs" variant="warning" onClick={() => onAction("stop", s)}>Stop</ActionButton>}
              <ActionButton size="xs" variant="ghost" icon="settings" onClick={() => onAction("configure", s)} aria-label="Configure stream" />
            </td>
          </tr>
        );
      })}
    </tbody>
  </table>
);

/* ── 1 · Command Center ─────────────────────────────────────────────────── */
function CommandCenter({ data, onAction }) {
  const m = data.metrics;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <HealthCard services={data.services} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <MetricStat label="Nodes Online" value={m.nodesOnline[0]} total={m.nodesOnline[1]} family="ok" icon="server" />
        <MetricStat label="Streams Live" value={m.streamsLive[0]} total={m.streamsLive[1]} family={m.streamsLive[0] < m.streamsLive[1] ? "warn" : "ok"} icon="video" />
        <MetricStat label="FDIR Events" value={m.fdirEvents} family="busy" icon="activity" hint="recent" />
        <MetricStat label="Open Alerts" value={m.openAlerts} family={m.openAlerts > 0 ? "warn" : "idle"} icon="bell" />
      </div>
      {/* Attention required — only when the live view-model surfaces one (else all-nominal). */}
      {data.attention ? (
      <section style={{ background: "var(--surface-card)", border: "1px solid var(--status-warn-border)", borderLeft: "var(--border-accent) solid var(--status-warn-solid)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <i data-lucide="triangle-alert" style={{ width: 16, height: 16, color: "var(--status-warn-solid)" }} />
          <h2 style={{ margin: 0, font: "var(--type-card-title)", color: "var(--text-strong)" }}>Attention Required</h2>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ font: "var(--type-mono-strong)", color: "var(--text-strong)" }}>{data.attention.binding}</span>
          <StatusBadge state={data.attention.status} size="sm" />
          {data.attention.error && <span style={{ font: "var(--type-body)", color: "var(--text-muted)" }}>last error: {data.attention.error}</span>}
          <div style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
            <ActionButton size="sm" variant="ghost" icon="stethoscope" onClick={() => onAction("diagnose", { binding: data.attention.binding })}>Diagnostics</ActionButton>
            <ActionButton size="sm" variant="default" icon="rotate-cw" onClick={() => onAction("restart", { binding: data.attention.binding })}>Restart</ActionButton>
            <ActionButton size="sm" variant="default" icon="wrench" onClick={() => onAction("maintenance", { binding: data.attention.binding })}>Maintenance</ActionButton>
          </div>
        </div>
      </section>
      ) : (
      <section style={{ background: "var(--surface-card)", border: "1px solid var(--status-ok-border)", borderLeft: "var(--border-accent) solid var(--status-ok-solid)", borderRadius: "var(--radius-lg)", padding: "14px 16px", display: "flex", alignItems: "center", gap: 8 }}>
        <i data-lucide="shield-check" style={{ width: 16, height: 16, color: "var(--status-ok-solid)" }} />
        <span style={{ font: "var(--type-card-title)", color: "var(--text-strong)" }}>All systems nominal</span>
        <span style={{ font: "var(--type-body)", color: "var(--text-muted)" }}>no attention items</span>
      </section>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16, alignItems: "start" }}>
        <Panel title="Live Streams" pad={false} style={{ overflow: "hidden" }}>
          <div style={{ padding: "14px 4px 6px" }}><StreamsTable rows={data.streams} onAction={onAction} /></div>
        </Panel>
        <Panel title="Recent events">
          <EventTimeline dense events={data.events.slice(0, 4)} />
        </Panel>
      </div>
    </div>
  );
}

/* ── 2 · Fleet ──────────────────────────────────────────────────────────── */
function FleetScreen({ data }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel title="Fleet state — desired ↔ actual"
        action={<>
          <ActionButton size="sm" variant="ghost" icon="play">Dry-run reconcile</ActionButton>
          <ActionButton size="sm" variant="primary" icon="git-merge">Apply reconcile</ActionButton>
          <ActionButton size="sm" variant="default" icon="download">Export plan</ActionButton>
        </>}>
        <DriftDiff
          desired={["cam10/color enabled", "cam10/depth enabled", "cam55/color enabled", "cam55/depth enabled"]}
          actual={["cam10/color online", "cam10/depth online", "cam55/color online", "!cam55/depth waiting_for_rtp"]}
          drift={["cam55/depth  desired=active  actual=waiting_for_rtp"]}
          style={{ border: "none", padding: 0 }} />
      </Panel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="Desired — fleet plan">
          <pre style={{ margin: 0, font: "var(--type-mono)", color: "var(--text-body)", whiteSpace: "pre-wrap" }}>{`cam10  local_gateway
  color  enabled
  depth  enabled

cam55  remote_producer
  color  enabled
  depth  enabled`}</pre>
        </Panel>
        <Panel title="Reconcile flow">
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", font: "var(--weight-semibold) var(--text-sm)/1 var(--font-sans)" }}>
            {["dry-run", "diff", "confirm", "apply", "verify", "audit"].map((s, i, a) => (
              <React.Fragment key={s}>
                <span style={{ padding: "6px 11px", borderRadius: "var(--radius-pill)", background: "var(--surface-sunken)", color: "var(--text-body)" }}>{s}</span>
                {i < a.length - 1 && <i data-lucide="arrow-right" style={{ width: 14, height: 14, color: "var(--text-faint)" }} />}
              </React.Fragment>
            ))}
          </div>
          <p style={{ marginTop: 14, font: "var(--type-body)", color: "var(--text-muted)" }}>Every apply-action follows this sequence. Nothing mutates without a diff and confirmation; every step lands in the audit log.</p>
        </Panel>
      </div>
    </div>
  );
}

/* ── 3 · Nodes ──────────────────────────────────────────────────────────── */
function NodesScreen({ data, onAction, onAddNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <p style={{ margin: 0, font: "var(--type-body)", color: "var(--text-muted)" }}>Local and remote nodes are managed the same way.</p>
        <ActionButton variant="primary" icon="plus" style={{ marginLeft: "auto" }} onClick={onAddNode}>Add node</ActionButton>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {data.nodes.map((n) => (
          <NodeCard key={n.nodeId} {...n}
            onCheck={() => onAction("check", n)} onProvision={() => onAction("provision", n)}
            onMaintenance={() => onAction("maintenance", n)} onRotate={() => onAction("rotate", n)}
            onOpenStreams={() => onAction("streams", n)} onRemove={() => onAction("remove-node", n)} />
        ))}
      </div>
    </div>
  );
}

/* ── 4 · Streams ────────────────────────────────────────────────────────── */
function StreamsScreen({ data, onAction }) {
  return (
    <Panel title="Streams — all bindings" pad={false}
      action={<ActionButton size="sm" variant="ghost" icon="refresh-cw">Refresh · 2s</ActionButton>}
      style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 4px 6px" }}><StreamsTable rows={data.streams} onAction={onAction} /></div>
    </Panel>
  );
}

/* ── 5 · Viewer Wall ────────────────────────────────────────────────────── */
function ViewerWall({ data }) {
  const [layout, setLayout] = React.useState("4up");
  const cols = layout === "1up" ? 1 : layout === "2up" ? 2 : 2;
  const shown = layout === "1up" ? data.streams.slice(0, 1) : layout === "2up" ? data.streams.slice(0, 2) : data.streams;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 6 }}>
        {[["1up", "square"], ["2up", "columns-2"], ["4up", "grid-2x2"]].map(([id, icon]) => (
          <ActionButton key={id} size="sm" variant={layout === id ? "primary" : "default"} icon={icon} onClick={() => setLayout(id)}>{id.replace("up", "-up")}</ActionButton>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 14 }}>
        {shown.map((s) => (
          <ViewerTile key={s.binding} binding={s.binding} status={s.status}
            rtpAge={s.status === "stale" ? "24s" : s.rtpAgeMs + "ms"} pinned={s.binding === "cam55:color"}
            fdirEvent={s.binding === "cam55:depth" ? "restart" : null}
            style={{ minHeight: layout === "1up" ? 420 : "auto" }} />
        ))}
      </div>
    </div>
  );
}

/* ── 6 · Diagnostics ────────────────────────────────────────────────────── */
function DiagnosticsScreen({ data }) {
  const [tab, setTab] = React.useState("overview");
  const tabs = [["overview", "Overview"], ["node", "Node"], ["stream", "Stream"], ["firewall", "RTP / Firewall"], ["fdir", "FDIR events"], ["audit", "Audit log"]];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 2, borderBottom: "1px solid var(--border-subtle)" }}>
        {tabs.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            style={{ padding: "9px 14px", border: "none", background: "transparent", cursor: "pointer",
              font: `${tab === id ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-base)/1 var(--font-sans)`,
              color: tab === id ? "var(--text-link)" : "var(--text-muted)",
              borderBottom: `2px solid ${tab === id ? "var(--blue-600)" : "transparent"}`, marginBottom: -1 }}>{label}</button>
        ))}
      </div>

      {tab === "overview" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <Panel title="Current incidents">
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {[["cam55/depth stale", "warn"], ["firewall drift detected", "warn"], ["viewer tokens unset", "warn"]].map(([t, f]) => (
                <div key={t} style={{ display: "flex", alignItems: "center", gap: 9 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "999px", background: `var(--status-${f}-solid)` }} />
                  <span style={{ font: "var(--type-mono)", color: "var(--text-body)" }}>{t}</span>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Recent events"><EventTimeline dense events={data.events} /></Panel>
        </div>
      )}

      {tab === "node" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <DiagnosticsPanel title="Agent" icon="cpu" rows={[
            { key: "reachable", value: "yes", status: "ok" }, { key: "version", value: "0.1.0" },
            { key: "last_seen", value: "8s", status: "ok" }, { key: "token_status", value: "valid", status: "ok" },
          ]} />
          <DiagnosticsPanel title="Camera" icon="camera" rows={[
            { key: "model", value: "RealSense D435" }, { key: "serial", value: "141722072135" },
            { key: "usb", value: "present", status: "ok" }, { key: "sensors", value: "color / depth" },
          ]} />
          <DiagnosticsPanel title="Services" icon="list-checks" rows={[
            { key: "node-agent", value: "active", status: "ok" }, { key: "realsense-mux", value: "active", status: "ok" },
            { key: "rs-stream@color", value: "active", status: "ok" }, { key: "rs-stream@depth", value: "failed", status: "failed" },
          ]} />
          <DiagnosticsPanel title="Control plane" icon="sliders-horizontal" rows={[
            { key: "fdir", value: "enabled", status: "ok" }, { key: "maintenance", value: "off" },
            { key: "last_restart", value: "14:22" }, { key: "last_error", value: "no_rtp", status: "warn" },
          ]} />
        </div>
      )}

      {tab === "stream" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <DiagnosticsPanel title="Binding" icon="link" rows={[
            { key: "binding_id", value: "cam55:color" }, { key: "mode", value: "remote_producer" },
            { key: "rtp_target", value: "192.168.1.10:5100" }, { key: "mountpoint", value: "2000" },
          ]} />
          <DiagnosticsPanel title="Data plane" icon="radio" rows={[
            { key: "rtp_packets", value: "yes", status: "ok" }, { key: "rtp_age_ms", value: "90" },
            { key: "janus_video_age", value: "100ms" }, { key: "webrtc_viewers", value: "1" },
          ]} />
        </div>
      )}

      {tab === "firewall" && (
        <Panel title="Firewall — expected ↔ actual"
          action={<>
            <ActionButton size="sm" variant="ghost" icon="play">Dry-run</ActionButton>
            <ActionButton size="sm" variant="warning" icon="shield-check">Apply</ActionButton>
          </>}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
            <div>
              <div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 8 }}>Expected</div>
              <pre style={{ margin: 0, font: "var(--type-mono)", color: "var(--text-body)", whiteSpace: "pre-wrap" }}>{`allow udp 192.168.1.55 → :5100
allow udp 192.168.1.55 → :5102`}</pre>
            </div>
            <div>
              <div style={{ font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 8 }}>Actual</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <StatusBadge family="ok" label="rule present" size="sm" />
                <StatusBadge family="ok" label="default drop enabled" size="sm" />
                <StatusBadge family="ok" label="janus admin not exposed" size="sm" />
              </div>
            </div>
          </div>
        </Panel>
      )}

      {tab === "fdir" && (
        <Panel title="FDIR events" pad={false} style={{ overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              {["Time", "Binding", "Domain", "Signal", "Action", "Result", "Suppressed", "Reason"].map((h) => (
                <th key={h} style={{ textAlign: "left", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "11px 14px", borderBottom: "1px solid var(--border-subtle)", background: "var(--surface-sunken)" }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {data.fdirEvents.map((e, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--slate-100)" }}>
                  <td style={tdMono}>{e.time}</td>
                  <td style={{ ...tdMono, color: "var(--text-strong)" }}>{e.binding}</td>
                  <td style={tdMono}>{e.domain}</td>
                  <td style={tdMono}>{e.signal}</td>
                  <td style={td}><StatusBadge family={e.action === "skipped" ? "idle" : "ok"} label={e.action} size="sm" dot={false} /></td>
                  <td style={td}>{e.result === "ok" ? <StatusBadge family="ok" label="ok" size="sm" /> : <span style={{ font: "var(--type-mono)", color: "var(--text-faint)" }}>{e.result}</span>}</td>
                  <td style={td}><StatusBadge family={e.suppressed === "yes" ? "busy" : "idle"} label={e.suppressed} size="sm" dot={false} /></td>
                  <td style={{ ...tdMono, color: "var(--text-muted)" }}>{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {tab === "audit" && (
        <Panel title="Audit log"><EventTimeline events={data.events} /></Panel>
      )}
    </div>
  );
}
const td = { padding: "10px 14px", verticalAlign: "middle" };
const tdMono = { ...td, font: "var(--type-mono)", color: "var(--text-body)" };

/* ── 7 · Settings / Security ────────────────────────────────────────────── */
function SettingsScreen({ data, onAction }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <DiagnosticsPanel title="Security" icon="shield" rows={data.security} />
        <DiagnosticsPanel title="Network" icon="network" rows={[
          { key: "gateway_lan_ip", value: data.gateway.lanIp }, { key: "camera_cidr", value: data.gateway.cidr },
          { key: "cloudflare", value: "connected", status: "ok" }, { key: "rtp_port_pool", value: "5000–5200" },
          { key: "firewall", value: "synced", status: "ok" },
        ]} />
      </div>
      {/* WebRTC media plane: STUN / TURN server + credential status (never the secret). */}
      <div>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
          <ActionButton size="sm" variant="default" icon="pencil" style={{ marginLeft: "auto" }}
            onClick={() => onAction && onAction("edit-webrtc")}>Edit WebRTC / TURN</ActionButton>
        </div>
        <DiagnosticsPanel title="WebRTC · STUN / TURN server" icon="radio-tower" rows={data.webrtc || []} />
      </div>
      <Panel title="Runtime config — live apply"
        action={<>
          <ActionButton size="sm" variant="ghost" icon="check">Validate</ActionButton>
          <ActionButton size="sm" variant="warning" icon="upload">Apply</ActionButton>
          <ActionButton size="sm" variant="ghost" icon="rotate-ccw">Rollback</ActionButton>
        </>}>
        <p style={{ margin: 0, font: "var(--type-body)", color: "var(--text-muted)" }}>Two-step, confirm-bound: validate a change, then apply the validated revision. No direct edit without validate + impact.</p>
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <StatusBadge family="warn" label="viewer tokens unset" size="sm" />
          <StatusBadge family="ok" label="ice_policy = relay (effective)" size="sm" />
        </div>
      </Panel>
    </div>
  );
}

window.SCREENS = { CommandCenter, FleetScreen, NodesScreen, StreamsScreen, ViewerWall, DiagnosticsScreen, SettingsScreen };
