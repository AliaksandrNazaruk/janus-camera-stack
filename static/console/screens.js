(() => {
  const C = window.GatewayConsoleDesignSystem_64aa70;
  const {
    StatusBadge,
    ActionButton,
    MetricStat,
    HealthCard,
    StreamRow,
    NodeCard,
    EventTimeline,
    DriftDiff,
    ViewerTile,
    DiagnosticsPanel
  } = C;
  function Panel({ title, action, children, pad = true, style }) {
    return /* @__PURE__ */ React.createElement("section", { style: { background: "var(--surface-card)", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-lg)", ...style } }, title && /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: "1px solid var(--border-subtle)" } }, /* @__PURE__ */ React.createElement("h2", { style: { margin: 0, font: "var(--type-card-title)", color: "var(--text-strong)" } }, title), /* @__PURE__ */ React.createElement("div", { style: { marginLeft: "auto", display: "flex", gap: 6 } }, action)), /* @__PURE__ */ React.createElement("div", { style: { padding: pad ? 16 : 0 } }, children));
  }
  const _stTd = { padding: "9px 12px", verticalAlign: "middle", font: "var(--type-mono)", color: "var(--text-body)" };
  const _stFmtAge = (ms) => ms == null ? "\u2014" : ms < 1e3 ? ms + "ms" : Math.round(ms / 100) / 10 + "s";
  const StreamsTable = ({ rows, onAction }) => /* @__PURE__ */ React.createElement("table", { style: { width: "100%", borderCollapse: "collapse" } }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, ["Stream", "Node", "Sensor", "Status", "RTP Age", "MP", "Port", "FDIR", "Last Error"].map((h) => /* @__PURE__ */ React.createElement("th", { key: h, style: { textAlign: "left", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "0 12px 9px", borderBottom: "1px solid var(--border-subtle)" } }, h)), /* @__PURE__ */ React.createElement("th", { style: { textAlign: "right", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "0 14px 9px", borderBottom: "1px solid var(--border-subtle)" } }, "Actions"))), /* @__PURE__ */ React.createElement("tbody", null, rows.map((s) => {
    const local = s.node === "cam10";
    const live = s.status === "online" || typeof s.rtpAgeMs === "number" && s.rtpAgeMs < 2e3;
    return /* @__PURE__ */ React.createElement("tr", { key: s.binding, style: { borderBottom: "1px solid var(--slate-100)" } }, /* @__PURE__ */ React.createElement("td", { style: { ..._stTd, color: "var(--text-strong)" } }, s.binding), /* @__PURE__ */ React.createElement("td", { style: _stTd }, s.node), /* @__PURE__ */ React.createElement("td", { style: _stTd }, s.sensor), /* @__PURE__ */ React.createElement("td", { style: { ..._stTd } }, /* @__PURE__ */ React.createElement(StatusBadge, { state: s.status, size: "sm" })), /* @__PURE__ */ React.createElement("td", { style: _stTd }, _stFmtAge(s.rtpAgeMs)), /* @__PURE__ */ React.createElement("td", { style: _stTd }, s.mountpoint), /* @__PURE__ */ React.createElement("td", { style: _stTd }, s.rtpPort), /* @__PURE__ */ React.createElement("td", { style: { ..._stTd } }, /* @__PURE__ */ React.createElement(StatusBadge, { family: s.fdir === "enabled" ? "ok" : "idle", label: s.fdir, size: "sm", dot: false })), /* @__PURE__ */ React.createElement("td", { style: { ..._stTd, color: "var(--text-muted)" } }, s.lastError || "\u2014"), /* @__PURE__ */ React.createElement("td", { style: { padding: "7px 14px", textAlign: "right", whiteSpace: "nowrap" } }, /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "ghost", icon: "external-link", onClick: () => onAction("open", s), "aria-label": "Open viewer" }), /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "default", icon: "rotate-cw", onClick: () => onAction("restart", s) }, "Restart"), live && /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "warning", onClick: () => onAction("stop", s) }, "Stop"), /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "ghost", icon: "settings", onClick: () => onAction("configure", s), "aria-label": "Configure stream" })));
  })));
  function CommandCenter({ data, onAction }) {
    const m = data.metrics;
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } }, /* @__PURE__ */ React.createElement(HealthCard, { services: data.services }), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 } }, /* @__PURE__ */ React.createElement(MetricStat, { label: "Nodes Online", value: m.nodesOnline[0], total: m.nodesOnline[1], family: "ok", icon: "server" }), /* @__PURE__ */ React.createElement(MetricStat, { label: "Streams Live", value: m.streamsLive[0], total: m.streamsLive[1], family: m.streamsLive[0] < m.streamsLive[1] ? "warn" : "ok", icon: "video" }), /* @__PURE__ */ React.createElement(MetricStat, { label: "FDIR Events", value: m.fdirEvents, family: "busy", icon: "activity", hint: "recent" }), /* @__PURE__ */ React.createElement(MetricStat, { label: "Open Alerts", value: m.openAlerts, family: m.openAlerts > 0 ? "warn" : "idle", icon: "bell" })), data.attention ? /* @__PURE__ */ React.createElement("section", { style: { background: "var(--surface-card)", border: "1px solid var(--status-warn-border)", borderLeft: "var(--border-accent) solid var(--status-warn-solid)", borderRadius: "var(--radius-lg)", padding: "14px 16px" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 8, marginBottom: 8 } }, /* @__PURE__ */ React.createElement("i", { "data-lucide": "triangle-alert", style: { width: 16, height: 16, color: "var(--status-warn-solid)" } }), /* @__PURE__ */ React.createElement("h2", { style: { margin: 0, font: "var(--type-card-title)", color: "var(--text-strong)" } }, "Attention Required")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" } }, /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-mono-strong)", color: "var(--text-strong)" } }, data.attention.binding), /* @__PURE__ */ React.createElement(StatusBadge, { state: data.attention.status, size: "sm" }), data.attention.error && /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-body)", color: "var(--text-muted)" } }, "last error: ", data.attention.error), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 6, marginLeft: "auto" } }, /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "stethoscope", onClick: () => onAction("diagnose", { binding: data.attention.binding }) }, "Diagnostics"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "default", icon: "rotate-cw", onClick: () => onAction("restart", { binding: data.attention.binding }) }, "Restart"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "default", icon: "wrench", onClick: () => onAction("maintenance", { binding: data.attention.binding }) }, "Maintenance")))) : /* @__PURE__ */ React.createElement("section", { style: { background: "var(--surface-card)", border: "1px solid var(--status-ok-border)", borderLeft: "var(--border-accent) solid var(--status-ok-solid)", borderRadius: "var(--radius-lg)", padding: "14px 16px", display: "flex", alignItems: "center", gap: 8 } }, /* @__PURE__ */ React.createElement("i", { "data-lucide": "shield-check", style: { width: 16, height: 16, color: "var(--status-ok-solid)" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-card-title)", color: "var(--text-strong)" } }, "All systems nominal"), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-body)", color: "var(--text-muted)" } }, "no attention items")), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16, alignItems: "start" } }, /* @__PURE__ */ React.createElement(Panel, { title: "Live Streams", pad: false, style: { overflow: "hidden" } }, /* @__PURE__ */ React.createElement("div", { style: { padding: "14px 4px 6px" } }, /* @__PURE__ */ React.createElement(StreamsTable, { rows: data.streams, onAction }))), /* @__PURE__ */ React.createElement(Panel, { title: "Recent events" }, /* @__PURE__ */ React.createElement(EventTimeline, { dense: true, events: data.events.slice(0, 4) }))));
  }
  function FleetScreen({ data }) {
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } }, /* @__PURE__ */ React.createElement(
      Panel,
      {
        title: "Fleet state \u2014 desired \u2194 actual",
        action: /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "play" }, "Dry-run reconcile"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "primary", icon: "git-merge" }, "Apply reconcile"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "default", icon: "download" }, "Export plan"))
      },
      /* @__PURE__ */ React.createElement(
        DriftDiff,
        {
          desired: ["cam10/color enabled", "cam10/depth enabled", "cam55/color enabled", "cam55/depth enabled"],
          actual: ["cam10/color online", "cam10/depth online", "cam55/color online", "!cam55/depth waiting_for_rtp"],
          drift: ["cam55/depth  desired=active  actual=waiting_for_rtp"],
          style: { border: "none", padding: 0 }
        }
      )
    ), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, /* @__PURE__ */ React.createElement(Panel, { title: "Desired \u2014 fleet plan" }, /* @__PURE__ */ React.createElement("pre", { style: { margin: 0, font: "var(--type-mono)", color: "var(--text-body)", whiteSpace: "pre-wrap" } }, `cam10  local_gateway
  color  enabled
  depth  enabled

cam55  remote_producer
  color  enabled
  depth  enabled`)), /* @__PURE__ */ React.createElement(Panel, { title: "Reconcile flow" }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", font: "var(--weight-semibold) var(--text-sm)/1 var(--font-sans)" } }, ["dry-run", "diff", "confirm", "apply", "verify", "audit"].map((s, i, a) => /* @__PURE__ */ React.createElement(React.Fragment, { key: s }, /* @__PURE__ */ React.createElement("span", { style: { padding: "6px 11px", borderRadius: "var(--radius-pill)", background: "var(--surface-sunken)", color: "var(--text-body)" } }, s), i < a.length - 1 && /* @__PURE__ */ React.createElement("i", { "data-lucide": "arrow-right", style: { width: 14, height: 14, color: "var(--text-faint)" } })))), /* @__PURE__ */ React.createElement("p", { style: { marginTop: 14, font: "var(--type-body)", color: "var(--text-muted)" } }, "Every apply-action follows this sequence. Nothing mutates without a diff and confirmation; every step lands in the audit log."))));
  }
  function NodesScreen({ data, onAction, onAddNode }) {
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center" } }, /* @__PURE__ */ React.createElement("p", { style: { margin: 0, font: "var(--type-body)", color: "var(--text-muted)" } }, "Local and remote nodes are managed the same way."), /* @__PURE__ */ React.createElement(ActionButton, { variant: "primary", icon: "plus", style: { marginLeft: "auto" }, onClick: onAddNode }, "Add node")), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, data.nodes.map((n) => /* @__PURE__ */ React.createElement(
      NodeCard,
      {
        key: n.nodeId,
        ...n,
        onCheck: () => onAction("check", n),
        onProvision: () => onAction("provision", n),
        onMaintenance: () => onAction("maintenance", n),
        onRotate: () => onAction("rotate", n),
        onOpenStreams: () => onAction("streams", n),
        onRemove: () => onAction("remove-node", n)
      }
    ))));
  }
  function StreamsScreen({ data, onAction }) {
    return /* @__PURE__ */ React.createElement(
      Panel,
      {
        title: "Streams \u2014 all bindings",
        pad: false,
        action: /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "refresh-cw" }, "Refresh \xB7 2s"),
        style: { overflow: "hidden" }
      },
      /* @__PURE__ */ React.createElement("div", { style: { padding: "14px 4px 6px" } }, /* @__PURE__ */ React.createElement(StreamsTable, { rows: data.streams, onAction }))
    );
  }
  function ViewerWall({ data }) {
    const [layout, setLayout] = React.useState("4up");
    const cols = layout === "1up" ? 1 : layout === "2up" ? 2 : 2;
    const shown = layout === "1up" ? data.streams.slice(0, 1) : layout === "2up" ? data.streams.slice(0, 2) : data.streams;
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 14 } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 6 } }, [["1up", "square"], ["2up", "columns-2"], ["4up", "grid-2x2"]].map(([id, icon]) => /* @__PURE__ */ React.createElement(ActionButton, { key: id, size: "sm", variant: layout === id ? "primary" : "default", icon, onClick: () => setLayout(id) }, id.replace("up", "-up")))), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 14 } }, shown.map((s) => /* @__PURE__ */ React.createElement(
      ViewerTile,
      {
        key: s.binding,
        binding: s.binding,
        status: s.status,
        rtpAge: s.status === "stale" ? "24s" : s.rtpAgeMs + "ms",
        pinned: s.binding === "cam55:color",
        fdirEvent: s.binding === "cam55:depth" ? "restart" : null,
        style: { minHeight: layout === "1up" ? 420 : "auto" }
      }
    ))));
  }
  function DiagnosticsScreen({ data }) {
    const [tab, setTab] = React.useState("overview");
    const tabs = [["overview", "Overview"], ["node", "Node"], ["stream", "Stream"], ["firewall", "RTP / Firewall"], ["fdir", "FDIR events"], ["audit", "Audit log"]];
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 2, borderBottom: "1px solid var(--border-subtle)" } }, tabs.map(([id, label]) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: id,
        onClick: () => setTab(id),
        style: {
          padding: "9px 14px",
          border: "none",
          background: "transparent",
          cursor: "pointer",
          font: `${tab === id ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-base)/1 var(--font-sans)`,
          color: tab === id ? "var(--text-link)" : "var(--text-muted)",
          borderBottom: `2px solid ${tab === id ? "var(--blue-600)" : "transparent"}`,
          marginBottom: -1
        }
      },
      label
    ))), tab === "overview" && /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, /* @__PURE__ */ React.createElement(Panel, { title: "Current incidents" }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 9 } }, [["cam55/depth stale", "warn"], ["firewall drift detected", "warn"], ["viewer tokens unset", "warn"]].map(([t, f]) => /* @__PURE__ */ React.createElement("div", { key: t, style: { display: "flex", alignItems: "center", gap: 9 } }, /* @__PURE__ */ React.createElement("span", { style: { width: 8, height: 8, borderRadius: "999px", background: `var(--status-${f}-solid)` } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-mono)", color: "var(--text-body)" } }, t))))), /* @__PURE__ */ React.createElement(Panel, { title: "Recent events" }, /* @__PURE__ */ React.createElement(EventTimeline, { dense: true, events: data.events }))), tab === "node" && /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Agent", icon: "cpu", rows: [
      { key: "reachable", value: "yes", status: "ok" },
      { key: "version", value: "0.1.0" },
      { key: "last_seen", value: "8s", status: "ok" },
      { key: "token_status", value: "valid", status: "ok" }
    ] }), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Camera", icon: "camera", rows: [
      { key: "model", value: "RealSense D435" },
      { key: "serial", value: "141722072135" },
      { key: "usb", value: "present", status: "ok" },
      { key: "sensors", value: "color / depth" }
    ] }), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Services", icon: "list-checks", rows: [
      { key: "node-agent", value: "active", status: "ok" },
      { key: "realsense-mux", value: "active", status: "ok" },
      { key: "rs-stream@color", value: "active", status: "ok" },
      { key: "rs-stream@depth", value: "failed", status: "failed" }
    ] }), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Control plane", icon: "sliders-horizontal", rows: [
      { key: "fdir", value: "enabled", status: "ok" },
      { key: "maintenance", value: "off" },
      { key: "last_restart", value: "14:22" },
      { key: "last_error", value: "no_rtp", status: "warn" }
    ] })), tab === "stream" && /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Binding", icon: "link", rows: [
      { key: "binding_id", value: "cam55:color" },
      { key: "mode", value: "remote_producer" },
      { key: "rtp_target", value: "192.168.1.10:5100" },
      { key: "mountpoint", value: "2000" }
    ] }), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Data plane", icon: "radio", rows: [
      { key: "rtp_packets", value: "yes", status: "ok" },
      { key: "rtp_age_ms", value: "90" },
      { key: "janus_video_age", value: "100ms" },
      { key: "webrtc_viewers", value: "1" }
    ] })), tab === "firewall" && /* @__PURE__ */ React.createElement(
      Panel,
      {
        title: "Firewall \u2014 expected \u2194 actual",
        action: /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "play" }, "Dry-run"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "warning", icon: "shield-check" }, "Apply"))
      },
      /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 } }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 8 } }, "Expected"), /* @__PURE__ */ React.createElement("pre", { style: { margin: 0, font: "var(--type-mono)", color: "var(--text-body)", whiteSpace: "pre-wrap" } }, `allow udp 192.168.1.55 \u2192 :5100
allow udp 192.168.1.55 \u2192 :5102`)), /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)", marginBottom: 8 } }, "Actual"), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } }, /* @__PURE__ */ React.createElement(StatusBadge, { family: "ok", label: "rule present", size: "sm" }), /* @__PURE__ */ React.createElement(StatusBadge, { family: "ok", label: "default drop enabled", size: "sm" }), /* @__PURE__ */ React.createElement(StatusBadge, { family: "ok", label: "janus admin not exposed", size: "sm" }))))
    ), tab === "fdir" && /* @__PURE__ */ React.createElement(Panel, { title: "FDIR events", pad: false, style: { overflow: "hidden" } }, /* @__PURE__ */ React.createElement("table", { style: { width: "100%", borderCollapse: "collapse" } }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, ["Time", "Binding", "Domain", "Signal", "Action", "Result", "Suppressed", "Reason"].map((h) => /* @__PURE__ */ React.createElement("th", { key: h, style: { textAlign: "left", font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-faint)", padding: "11px 14px", borderBottom: "1px solid var(--border-subtle)", background: "var(--surface-sunken)" } }, h)))), /* @__PURE__ */ React.createElement("tbody", null, data.fdirEvents.map((e, i) => /* @__PURE__ */ React.createElement("tr", { key: i, style: { borderBottom: "1px solid var(--slate-100)" } }, /* @__PURE__ */ React.createElement("td", { style: tdMono }, e.time), /* @__PURE__ */ React.createElement("td", { style: { ...tdMono, color: "var(--text-strong)" } }, e.binding), /* @__PURE__ */ React.createElement("td", { style: tdMono }, e.domain), /* @__PURE__ */ React.createElement("td", { style: tdMono }, e.signal), /* @__PURE__ */ React.createElement("td", { style: td }, /* @__PURE__ */ React.createElement(StatusBadge, { family: e.action === "skipped" ? "idle" : "ok", label: e.action, size: "sm", dot: false })), /* @__PURE__ */ React.createElement("td", { style: td }, e.result === "ok" ? /* @__PURE__ */ React.createElement(StatusBadge, { family: "ok", label: "ok", size: "sm" }) : /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-mono)", color: "var(--text-faint)" } }, e.result)), /* @__PURE__ */ React.createElement("td", { style: td }, /* @__PURE__ */ React.createElement(StatusBadge, { family: e.suppressed === "yes" ? "busy" : "idle", label: e.suppressed, size: "sm", dot: false })), /* @__PURE__ */ React.createElement("td", { style: { ...tdMono, color: "var(--text-muted)" } }, e.reason)))))), tab === "audit" && /* @__PURE__ */ React.createElement(Panel, { title: "Audit log" }, /* @__PURE__ */ React.createElement(EventTimeline, { events: data.events })));
  }
  const td = { padding: "10px 14px", verticalAlign: "middle" };
  const tdMono = { ...td, font: "var(--type-mono)", color: "var(--text-body)" };
  function SettingsScreen({ data, onAction }) {
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 16 } }, /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 } }, /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Security", icon: "shield", rows: data.security }), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "Network", icon: "network", rows: [
      { key: "gateway_lan_ip", value: data.gateway.lanIp },
      { key: "camera_cidr", value: data.gateway.cidr },
      { key: "cloudflare", value: "connected", status: "ok" },
      { key: "rtp_port_pool", value: "5000\u20135200" },
      { key: "firewall", value: "synced", status: "ok" }
    ] })), /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", marginBottom: 8 } }, /* @__PURE__ */ React.createElement(
      ActionButton,
      {
        size: "sm",
        variant: "default",
        icon: "pencil",
        style: { marginLeft: "auto" },
        onClick: () => onAction && onAction("edit-webrtc")
      },
      "Edit WebRTC / TURN"
    )), /* @__PURE__ */ React.createElement(DiagnosticsPanel, { title: "WebRTC \xB7 STUN / TURN server", icon: "radio-tower", rows: data.webrtc || [] })), /* @__PURE__ */ React.createElement(
      Panel,
      {
        title: "Runtime config \u2014 live apply",
        action: /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "check" }, "Validate"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "warning", icon: "upload" }, "Apply"), /* @__PURE__ */ React.createElement(ActionButton, { size: "sm", variant: "ghost", icon: "rotate-ccw" }, "Rollback"))
      },
      /* @__PURE__ */ React.createElement("p", { style: { margin: 0, font: "var(--type-body)", color: "var(--text-muted)" } }, "Two-step, confirm-bound: validate a change, then apply the validated revision. No direct edit without validate + impact."),
      /* @__PURE__ */ React.createElement("div", { style: { marginTop: 12, display: "flex", flexDirection: "column", gap: 8 } }, /* @__PURE__ */ React.createElement(StatusBadge, { family: "warn", label: "viewer tokens unset", size: "sm" }), /* @__PURE__ */ React.createElement(StatusBadge, { family: "ok", label: "ice_policy = relay (effective)", size: "sm" }))
    ));
  }
  window.SCREENS = { CommandCenter, FleetScreen, NodesScreen, StreamsScreen, ViewerWall, DiagnosticsScreen, SettingsScreen };
})();
