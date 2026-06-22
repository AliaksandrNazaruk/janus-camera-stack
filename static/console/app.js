(() => {
  const C = window.GatewayConsoleDesignSystem_64aa70;
  const { AlertBar, OperationDrawer, ConfirmDialog, ActionButton, StatusBadge } = C;
  const { Sidebar, Topbar, SCREENS } = window;
  const enc = encodeURIComponent;
  const ADMIN = "/api/v1/admin";
  let _login = null;
  function login() {
    if (_login) return _login;
    _login = (async () => {
      const tok = window.prompt("Admin token (CAM_ADMIN_TOKEN):");
      if (!tok) return false;
      const r = await fetch("/api/v1/ui/session", { method: "POST", credentials: "include", headers: { "X-Admin-Token": tok } });
      return r.ok;
    })();
    return _login.finally(() => {
      _login = null;
    });
  }
  async function api(url, init) {
    const opts = Object.assign({ credentials: "include" }, init || {});
    let r = await fetch(url, opts);
    if (r.status === 401 || r.status === 403) {
      if (await login()) r = await fetch(url, opts);
    }
    return r;
  }
  const TITLES = {
    command: ["Command Center"],
    fleet: ["Fleet"],
    nodes: ["Nodes"],
    streams: ["Streams"],
    viewer: ["Viewer Wall"],
    diagnostics: ["Diagnostics"],
    settings: ["Settings", "Security"]
  };
  const OP_STEPS = {
    restart: ["Request sent", "Node acknowledged", "RTP resumed"],
    stop: ["Request sent", "Encoder stopped", "Marked stopped (desired down)"],
    maintenance: ["Request sent", "State persisted"],
    check: ["Probe agent", "Reachability recorded"],
    provision: ["Bundle pushed", "Token issued", "rs-stream restarted"],
    rotate: ["New token issued", "Agent re-authenticated"],
    "remove-node": ["Mountpoints destroyed", "Bindings removed", "Firewall reconciled"]
  };
  function opSpec(kind, target) {
    const binding = target.binding || (target.sensor && target.node ? `${target.node}:${target.sensor}` : null);
    const nodeId = target.nodeId;
    const id = binding || nodeId || "";
    switch (kind) {
      case "restart":
        return {
          kind,
          title: `Restart ${id}`,
          target: id,
          impactClass: "B",
          impact: ["Stream reconnects (5\u201315s)", "Viewers may briefly drop", "FDIR stays enabled"],
          fdirNote: "stays enabled",
          duration: "5\u201315s",
          confirmLabel: "Restart",
          endpoint: `${ADMIN}/stream-bindings/${enc(binding)}/restart`,
          method: "POST"
        };
      case "maintenance": {
        const on = target.health && target.health.maintenance === "on";
        return {
          kind,
          title: `${on ? "End maintenance" : "Maintenance"} \u2014 ${id}`,
          target: id,
          impactClass: "B",
          impact: on ? ["FDIR resumes monitoring this node"] : ["FDIR is suppressed for this node while servicing hw", "Streams keep running"],
          fdirNote: on ? "resumes" : "suppressed",
          duration: "instant",
          confirmLabel: on ? "End maintenance" : "Enable maintenance",
          endpoint: `${ADMIN}/nodes/${enc(nodeId)}/maintenance`,
          method: "POST",
          body: { enabled: !on }
        };
      }
      case "check":
        return {
          kind,
          title: `Check node ${id}`,
          target: id,
          impactClass: "A",
          impact: ["Read-only probe of the node agent (reachability + last-seen)"],
          duration: "2\u20134s",
          confirmLabel: "Run check",
          endpoint: `${ADMIN}/nodes/check`,
          method: "POST",
          body: { node_id: nodeId }
        };
      case "provision":
        return {
          kind,
          title: `Provision ${id}`,
          target: id,
          impactClass: "C",
          impact: ["Pushes node bundle + token over SSH", "rs-stream services restart (brief blip)", "Requires the host key to be pinned first"],
          fdirNote: "re-enabled after",
          duration: "20\u201340s",
          confirmLabel: "Provision",
          endpoint: `${ADMIN}/nodes/${enc(nodeId)}/provision`,
          method: "POST",
          needsSudo: true
        };
      case "rotate":
        return {
          kind,
          title: `Rotate token \u2014 ${id}`,
          target: id,
          impactClass: "B",
          impact: ["Issues a new node-agent token", "Old token invalidated; agent restarts (no stream blip)"],
          duration: "instant",
          confirmLabel: "Rotate token",
          endpoint: `${ADMIN}/nodes/${enc(nodeId)}/rotate-token`,
          method: "POST",
          needsSudo: true
        };
      default:
        return null;
    }
  }
  function Centered({ children }) {
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 14, background: "var(--surface-app)" } }, children);
  }
  function LoadingScreen() {
    return /* @__PURE__ */ React.createElement(Centered, null, /* @__PURE__ */ React.createElement("i", { "data-lucide": "loader", style: { width: 28, height: 28, color: "var(--blue-600)", animation: "gc-spin 1s linear infinite" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-body)", color: "var(--text-muted)" } }, "Loading fleet\u2026"));
  }
  function ErrorScreen({ err, onRetry }) {
    React.useEffect(() => {
      window.lucide && window.lucide.createIcons();
    });
    return /* @__PURE__ */ React.createElement(Centered, null, /* @__PURE__ */ React.createElement("i", { "data-lucide": "octagon-alert", style: { width: 28, height: 28, color: "var(--status-bad-solid)" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-card-title)", color: "var(--text-strong)" } }, "Could not load the fleet view-model"), /* @__PURE__ */ React.createElement("code", { style: { font: "var(--type-mono)", color: "var(--text-muted)" } }, err), /* @__PURE__ */ React.createElement(ActionButton, { variant: "primary", icon: "rotate-cw", onClick: onRetry }, "Retry"), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-xs)", color: "var(--text-faint)" } }, "If this is 401/403, click Retry and enter the admin token when prompted."));
  }
  function Wizard({ onClose, onAdded }) {
    const [host, setHost] = React.useState("");
    const [name, setName] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [msg, setMsg] = React.useState(null);
    React.useEffect(() => {
      window.lucide && window.lucide.createIcons();
    });
    const Field = ({ label, value, onChange, placeholder }) => /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 5 } }, /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" } }, label), /* @__PURE__ */ React.createElement(
      "input",
      {
        value,
        placeholder,
        onChange: (e) => onChange(e.target.value),
        style: { font: "var(--type-mono)", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-default)", color: "var(--text-strong)" }
      }
    ));
    async function add() {
      if (!host.trim()) {
        setMsg({ f: "warn", t: "Enter the node IPv4" });
        return;
      }
      setBusy(true);
      try {
        const r = await api(`${ADMIN}/nodes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: host.trim(), display_name: name.trim() || null })
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          setMsg({ f: "bad", t: j.detail || `HTTP ${r.status}` });
          setBusy(false);
          return;
        }
        setMsg({ f: "ok", t: `added ${j.node_id} \u2014 confirm host key + provision next` });
        onAdded && onAdded();
        setTimeout(onClose, 1100);
      } catch (e) {
        setMsg({ f: "bad", t: String(e && e.message || e) });
        setBusy(false);
      }
    }
    return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", inset: 0, zIndex: 240, display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "10vh" } }, /* @__PURE__ */ React.createElement("div", { onClick: onClose, style: { position: "absolute", inset: 0, background: "var(--surface-overlay)" } }), /* @__PURE__ */ React.createElement("div", { style: { position: "relative", width: 520, maxWidth: "94vw", background: "var(--surface-card)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-overlay)", overflow: "hidden", animation: "gc-pop 160ms ease" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, padding: "16px 18px", borderBottom: "1px solid var(--border-subtle)" } }, /* @__PURE__ */ React.createElement("i", { "data-lucide": "server-cog", style: { width: 18, height: 18, color: "var(--blue-600)" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-section)", color: "var(--text-strong)" } }, "Add node"), /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "ghost", icon: "x", style: { marginLeft: "auto" }, onClick: onClose })), /* @__PURE__ */ React.createElement("div", { style: { padding: "18px", display: "grid", gap: 12 } }, /* @__PURE__ */ React.createElement(Field, { label: "Host / IPv4", value: host, onChange: setHost, placeholder: "192.168.1.55" }), /* @__PURE__ */ React.createElement(Field, { label: "Display name (optional)", value: name, onChange: setName, placeholder: "Arm RealSense #1" }), msg && /* @__PURE__ */ React.createElement(StatusBadge, { family: msg.f, label: msg.t, size: "sm" }), /* @__PURE__ */ React.createElement("p", { style: { font: "var(--type-xs)", color: "var(--text-muted)", margin: 0 } }, "Registers the host on the gateway. Then confirm its SSH host key and Provision from its node card (full guided onboarding: ", /* @__PURE__ */ React.createElement("code", { style: { font: "var(--type-mono)" } }, "/camera_hosts.html"), ").")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8, padding: "14px 18px", borderTop: "1px solid var(--border-subtle)", justifyContent: "flex-end" } }, /* @__PURE__ */ React.createElement(ActionButton, { variant: "ghost", onClick: onClose }, "Cancel"), /* @__PURE__ */ React.createElement(ActionButton, { variant: "primary", icon: "plus", busy, onClick: add }, "Add node"))));
  }
  function StreamTuneForm({ target, onClose, onSaved }) {
    const serial = (target.binding || "").split(":")[0];
    const sensor = target.sensor;
    const local = target.node === "cam10";
    const cfgUrl = local ? `/cameras/${enc(serial)}/${enc(sensor)}/config` : `${ADMIN}/stream-bindings/${enc(target.binding)}/tuning`;
    const modesUrl = local ? `/cameras/${enc(serial)}/${enc(sensor)}/modes` : `${ADMIN}/stream-bindings/${enc(target.binding)}/modes`;
    const [cfg, setCfg] = React.useState(null);
    const [modes, setModes] = React.useState([]);
    const [busy, setBusy] = React.useState(false);
    const [msg, setMsg] = React.useState(null);
    React.useEffect(() => {
      api(cfgUrl).then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)).then(setCfg).catch((e) => setMsg({ f: "bad", t: "config load failed: " + e }));
      if (modesUrl) {
        api(modesUrl).then((r) => r.ok ? r.json() : { modes: [] }).then((m) => setModes(m && m.modes || [])).catch(() => {
        });
      }
    }, []);
    React.useEffect(() => {
      window.lucide && window.lucide.createIcons();
    });
    const resOptions = Array.from(new Set(modes.map((m) => `${m.width}x${m.height}`)));
    const curRes = cfg ? `${cfg.width}x${cfg.height}` : "";
    const fpsOptions = (() => {
      const m = modes.find((x) => `${x.width}x${x.height}` === curRes);
      return m ? m.fps : [];
    })();
    const setRes = (wxh) => {
      const p = wxh.split("x");
      setCfg((c) => Object.assign({}, c, { width: parseInt(p[0], 10), height: parseInt(p[1], 10) }));
    };
    const set = (k, v) => setCfg((c) => Object.assign({}, c, { [k]: v }));
    async function save() {
      setBusy(true);
      try {
        const r = await api(cfgUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          setMsg({ f: "bad", t: j.detail || "HTTP " + r.status });
          setBusy(false);
          return;
        }
        setMsg({ f: "ok", t: "applied \u2014 encoder restarted" });
        onSaved && onSaved();
        setTimeout(onClose, 1100);
      } catch (e) {
        setMsg({ f: "bad", t: String(e && e.message || e) });
        setBusy(false);
      }
    }
    const lbl = { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" };
    const inp = { font: "var(--type-mono)", padding: "7px 9px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-default)", color: "var(--text-strong)" };
    return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", inset: 0, zIndex: 240, display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "8vh" } }, /* @__PURE__ */ React.createElement("div", { onClick: onClose, style: { position: "absolute", inset: 0, background: "var(--surface-overlay)" } }), /* @__PURE__ */ React.createElement("div", { style: { position: "relative", width: 480, maxWidth: "94vw", background: "var(--surface-card)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-overlay)", overflow: "hidden", animation: "gc-pop 160ms ease" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, padding: "16px 18px", borderBottom: "1px solid var(--border-subtle)" } }, /* @__PURE__ */ React.createElement("i", { "data-lucide": "settings", style: { width: 18, height: 18, color: "var(--blue-600)" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-section)", color: "var(--text-strong)" } }, "Tune ", target.binding), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-xs)", color: "var(--text-faint)", marginLeft: 6 } }, "Class C"), /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "ghost", icon: "x", style: { marginLeft: "auto" }, onClick: onClose })), !cfg ? /* @__PURE__ */ React.createElement("div", { style: { padding: 24, font: "var(--type-body)", color: "var(--text-muted)" } }, "Loading\u2026") : /* @__PURE__ */ React.createElement("div", { style: { padding: "18px", display: "grid", gap: 14 } }, /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4 } }, /* @__PURE__ */ React.createElement("span", { style: lbl }, "Resolution"), /* @__PURE__ */ React.createElement("select", { value: curRes, onChange: (e) => setRes(e.target.value), style: inp }, resOptions.length === 0 && /* @__PURE__ */ React.createElement("option", { value: curRes }, curRes), resOptions.map((r) => /* @__PURE__ */ React.createElement("option", { key: r, value: r }, r)))), /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4 } }, /* @__PURE__ */ React.createElement("span", { style: lbl }, "Frame rate (fps)"), /* @__PURE__ */ React.createElement("select", { value: cfg.fps, onChange: (e) => set("fps", parseInt(e.target.value, 10)), style: inp }, fpsOptions.length === 0 && /* @__PURE__ */ React.createElement("option", { value: cfg.fps }, cfg.fps), fpsOptions.map((f) => /* @__PURE__ */ React.createElement("option", { key: f, value: f }, f)))), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: 6 } }, /* @__PURE__ */ React.createElement("span", { style: lbl }, "Rotation"), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 6 } }, [0, 90, 180, 270].map((deg) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: deg,
        onClick: () => set("rotation", deg),
        style: {
          flex: 1,
          padding: "7px 0",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          border: "1px solid " + (cfg.rotation === deg ? "var(--blue-600)" : "var(--border-default)"),
          background: cfg.rotation === deg ? "var(--blue-600)" : "var(--surface-card)",
          color: cfg.rotation === deg ? "#fff" : "var(--text-body)",
          font: "var(--type-mono)"
        }
      },
      deg,
      "\xB0"
    )))), /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4 } }, /* @__PURE__ */ React.createElement("span", { style: lbl }, "Bitrate (kbps)"), /* @__PURE__ */ React.createElement(
      "input",
      {
        type: "number",
        min: "200",
        max: "20000",
        step: "100",
        value: cfg.bitrate_kbps,
        onChange: (e) => set("bitrate_kbps", parseInt(e.target.value, 10) || 0),
        style: inp
      }
    )), msg && /* @__PURE__ */ React.createElement(StatusBadge, { family: msg.f, label: msg.t, size: "sm" }), /* @__PURE__ */ React.createElement("p", { style: { margin: 0, font: "var(--type-xs)", color: "var(--text-muted)" } }, "Applying rewrites the encoder tuning and ", /* @__PURE__ */ React.createElement("b", null, "restarts the encoder"), " \u2014 the stream is offline ~5\u201320s; viewers reconnect.")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8, padding: "14px 18px", borderTop: "1px solid var(--border-subtle)", justifyContent: "flex-end" } }, /* @__PURE__ */ React.createElement(ActionButton, { variant: "ghost", onClick: onClose }, "Cancel"), /* @__PURE__ */ React.createElement(ActionButton, { variant: "warning", icon: "upload", busy, disabled: !cfg, onClick: save }, "Apply & restart"))));
  }
  function WebRtcForm({ onClose, onSaved }) {
    const [cfg, setCfg] = React.useState(null);
    const [pwd, setPwd] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [msg, setMsg] = React.useState(null);
    React.useEffect(() => {
      api("/janus/nat").then((r) => r.json()).then((c) => setCfg(c)).catch((e) => setMsg({ f: "bad", t: "load failed: " + e }));
    }, []);
    React.useEffect(() => {
      window.lucide && window.lucide.createIcons();
    });
    const set = (k, v) => setCfg((c) => Object.assign({}, c, { [k]: v }));
    const Field = ({ label, k, type, w }) => /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4 } }, /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" } }, label), /* @__PURE__ */ React.createElement(
      "input",
      {
        type: type || "text",
        value: cfg[k] == null ? "" : cfg[k],
        onChange: (e) => set(k, type === "number" ? parseInt(e.target.value, 10) || 0 : e.target.value),
        style: { font: "var(--type-mono)", padding: "7px 9px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-default)", color: "var(--text-strong)", width: w || "auto" }
      }
    ));
    async function save() {
      setBusy(true);
      try {
        const body = Object.assign({}, cfg, { turn_pwd: pwd });
        const r = await api("/janus/nat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          setMsg({ f: "bad", t: j.detail || "HTTP " + r.status });
          setBusy(false);
          return;
        }
        setMsg({ f: "ok", t: "saved \u2014 Janus restarted; viewers reconnect" });
        onSaved && onSaved();
        setTimeout(onClose, 1200);
      } catch (e) {
        setMsg({ f: "bad", t: String(e && e.message || e) });
        setBusy(false);
      }
    }
    return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", inset: 0, zIndex: 240, display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "7vh" } }, /* @__PURE__ */ React.createElement("div", { onClick: onClose, style: { position: "absolute", inset: 0, background: "var(--surface-overlay)" } }), /* @__PURE__ */ React.createElement("div", { style: { position: "relative", width: 560, maxWidth: "94vw", background: "var(--surface-card)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-overlay)", overflow: "hidden", animation: "gc-pop 160ms ease" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 10, padding: "16px 18px", borderBottom: "1px solid var(--border-subtle)" } }, /* @__PURE__ */ React.createElement("i", { "data-lucide": "radio-tower", style: { width: 18, height: 18, color: "var(--blue-600)" } }), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-section)", color: "var(--text-strong)" } }, "WebRTC \xB7 STUN / TURN"), /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-xs)", color: "var(--text-faint)", marginLeft: 6 } }, "Class C"), /* @__PURE__ */ React.createElement(ActionButton, { size: "xs", variant: "ghost", icon: "x", style: { marginLeft: "auto" }, onClick: onClose })), !cfg ? /* @__PURE__ */ React.createElement("div", { style: { padding: 24, font: "var(--type-body)", color: "var(--text-muted)" } }, "Loading\u2026") : /* @__PURE__ */ React.createElement("div", { style: { padding: "18px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 } }, /* @__PURE__ */ React.createElement(Field, { label: "STUN server", k: "stun_server" }), /* @__PURE__ */ React.createElement(Field, { label: "STUN port", k: "stun_port", type: "number" }), /* @__PURE__ */ React.createElement(Field, { label: "TURN server", k: "turn_server" }), /* @__PURE__ */ React.createElement(Field, { label: "TURN port", k: "turn_port", type: "number" }), /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4 } }, /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" } }, "TURN transport"), /* @__PURE__ */ React.createElement(
      "select",
      {
        value: cfg.turn_type,
        onChange: (e) => set("turn_type", e.target.value),
        style: { font: "var(--type-mono)", padding: "7px 9px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-default)", color: "var(--text-strong)" }
      },
      /* @__PURE__ */ React.createElement("option", { value: "udp" }, "udp"),
      /* @__PURE__ */ React.createElement("option", { value: "tcp" }, "tcp"),
      /* @__PURE__ */ React.createElement("option", { value: "tls" }, "tls")
    )), /* @__PURE__ */ React.createElement(Field, { label: "TURN user", k: "turn_user" }), /* @__PURE__ */ React.createElement("label", { style: { display: "flex", flexDirection: "column", gap: 4, gridColumn: "1 / -1" } }, /* @__PURE__ */ React.createElement("span", { style: { font: "var(--type-label)", textTransform: "uppercase", letterSpacing: "var(--tracking-label)", color: "var(--text-muted)" } }, "TURN password / shared secret (blank = keep)"), /* @__PURE__ */ React.createElement(
      "input",
      {
        type: "password",
        value: pwd,
        placeholder: "\u2022\u2022\u2022\u2022 unchanged",
        onChange: (e) => setPwd(e.target.value),
        style: { font: "var(--type-mono)", padding: "7px 9px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-default)", color: "var(--text-strong)" }
      }
    )), /* @__PURE__ */ React.createElement(Field, { label: "ICE min port", k: "min_port", type: "number" }), /* @__PURE__ */ React.createElement(Field, { label: "ICE max port", k: "max_port", type: "number" }), /* @__PURE__ */ React.createElement("label", { style: { display: "flex", alignItems: "center", gap: 8, gridColumn: "1 / -1", font: "var(--type-body)", color: "var(--text-body)" } }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: !!cfg.ice_tcp, onChange: (e) => set("ice_tcp", e.target.checked) }), " ICE over TCP"), msg && /* @__PURE__ */ React.createElement("div", { style: { gridColumn: "1 / -1" } }, /* @__PURE__ */ React.createElement(StatusBadge, { family: msg.f, label: msg.t, size: "sm" })), /* @__PURE__ */ React.createElement("p", { style: { gridColumn: "1 / -1", margin: 0, font: "var(--type-xs)", color: "var(--text-muted)" } }, "Saving rewrites janus.jcfg and ", /* @__PURE__ */ React.createElement("b", null, "restarts Janus"), " \u2014 every live viewer reconnects (a few seconds).")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8, padding: "14px 18px", borderTop: "1px solid var(--border-subtle)", justifyContent: "flex-end" } }, /* @__PURE__ */ React.createElement(ActionButton, { variant: "ghost", onClick: onClose }, "Cancel"), /* @__PURE__ */ React.createElement(ActionButton, { variant: "warning", icon: "upload", busy, disabled: !cfg, onClick: save }, "Save & restart Janus"))));
  }
  function App() {
    const [route, setRoute] = React.useState("command");
    const [role, setRole] = React.useState("Operator");
    const [refreshing, setRefreshing] = React.useState(false);
    const [data, setData] = React.useState(null);
    const [err, setErr] = React.useState(null);
    const [alertOpen, setAlertOpen] = React.useState(true);
    const [op, setOp] = React.useState(null);
    const [confirm, setConfirm] = React.useState(null);
    const [wizard, setWizard] = React.useState(false);
    const [webrtcOpen, setWebrtcOpen] = React.useState(false);
    const [streamTune, setStreamTune] = React.useState(null);
    const load = React.useCallback(async () => {
      try {
        const r = await api(`/api/v1/ui/fleet`);
        if (!r.ok) {
          setErr(`HTTP ${r.status}`);
          return;
        }
        setData(await r.json());
        setErr(null);
      } catch (e) {
        setErr(String(e && e.message || e));
      }
    }, []);
    React.useEffect(() => {
      load();
      const t = setInterval(load, 5e3);
      return () => clearInterval(t);
    }, [load]);
    React.useEffect(() => {
      window.lucide && window.lucide.createIcons();
    });
    const refresh = () => {
      setRefreshing(true);
      load().finally(() => setTimeout(() => setRefreshing(false), 400));
    };
    async function runOp(spec) {
      if (!spec) return;
      let body = spec.body;
      if (spec.needsSudo) {
        const pw = window.prompt("node sudo password (held in memory for the run, never stored):");
        if (pw === null) {
          setOp(null);
          return;
        }
        body = Object.assign({}, body || {}, { sudo_password: pw });
      }
      const labels = OP_STEPS[spec.kind] || ["Request sent", "Done"];
      setOp({ preset: spec, steps: labels.map((l, i) => ({ label: l, state: i === 0 ? "active" : "pending" })), running: true, result: null });
      try {
        var init = { method: spec.method || "POST" };
        if (body) {
          init.headers = { "Content-Type": "application/json" };
          init.body = JSON.stringify(body);
        }
        const r = await api(spec.endpoint, init);
        let ok = r.ok, detail = "";
        try {
          const j = await r.json();
          if (j && j.ok === false) ok = false;
          detail = j && (j.detail || j.reason || j.message) || (ok ? "done" : `HTTP ${r.status}`);
        } catch (_) {
          detail = ok ? "done" : `HTTP ${r.status}`;
        }
        setOp((cur) => {
          if (!cur) return cur;
          const steps = cur.steps.map((s) => ({ label: s.label, state: ok ? "ok" : "failed" }));
          if (!ok) steps.push({ label: detail, state: "failed" });
          return { ...cur, steps, running: false, result: ok ? "ok" : "failed" };
        });
        if (ok) refresh();
      } catch (e) {
        setOp((cur) => cur ? { ...cur, running: false, result: "failed", steps: (cur.steps || []).concat([{ label: String(e && e.message || e), state: "failed" }]) } : cur);
      }
    }
    function openViewer(target) {
      const mp = target.mountpoint || target.mp;
      if (!mp) {
        setRoute("viewer");
        return;
      }
      window.open(`/preview/${enc(mp)}`, "_blank", "noopener");
    }
    function onAction(kind, target) {
      if (kind === "diagnose") {
        setRoute("diagnostics");
        return;
      }
      if (kind === "open") {
        openViewer(target);
        return;
      }
      if (kind === "streams") {
        setRoute("streams");
        return;
      }
      if (kind === "edit-webrtc") {
        setWebrtcOpen(true);
        return;
      }
      if (kind === "configure") {
        setStreamTune(target);
        return;
      }
      if (kind === "stop") {
        setConfirm({
          title: `Stop ${target.binding}`,
          message: "Stopping marks this stream desired-down; the monitor won't restart it (FDIR is left unchanged).",
          impact: ["Stream goes offline immediately", "Marked stopped (desired down)", "Restart to resume — FDIR setting is unchanged"],
          confirmLabel: "Stop stream",
          _spec: { kind: "stop", endpoint: `${ADMIN}/stream-bindings/${enc(target.binding)}/stop`, method: "POST" }
        });
        return;
      }
      if (kind === "remove-node") {
        const id = target.nodeId;
        setConfirm({
          title: `Remove node ${id}`,
          destructive: true,
          confirmPhrase: id,
          willRemove: (target.streams || []).map((s) => `${id}:${s.sensor} binding + mp ${s.mp}`).concat([`firewall rules \u2192 ${target.host}`, "pinned host key + node token"]),
          willKeep: ["Gateway cam10 + its streams", "Audit history"],
          rollback: "Re-onboard via Add node.",
          confirmLabel: "Remove node",
          _spec: { kind: "remove-node", endpoint: `${ADMIN}/nodes/${enc(id)}`, method: "DELETE" }
        });
        return;
      }
      const spec = opSpec(kind, target);
      if (spec) setOp({ preset: spec, steps: null, running: false, result: null });
    }
    function confirmRun() {
      const spec = confirm && confirm._spec;
      setConfirm(null);
      if (spec) runOp(spec);
    }
    if (err && !data) return /* @__PURE__ */ React.createElement(ErrorScreen, { err, onRetry: load });
    if (!data) return /* @__PURE__ */ React.createElement(LoadingScreen, null);
    const Screen = {
      command: /* @__PURE__ */ React.createElement(SCREENS.CommandCenter, { data, onAction }),
      fleet: /* @__PURE__ */ React.createElement(SCREENS.FleetScreen, { data }),
      nodes: /* @__PURE__ */ React.createElement(SCREENS.NodesScreen, { data, onAction, onAddNode: () => setWizard(true) }),
      streams: /* @__PURE__ */ React.createElement(SCREENS.StreamsScreen, { data, onAction }),
      viewer: /* @__PURE__ */ React.createElement(SCREENS.ViewerWall, { data }),
      diagnostics: /* @__PURE__ */ React.createElement(SCREENS.DiagnosticsScreen, { data }),
      settings: /* @__PURE__ */ React.createElement(SCREENS.SettingsScreen, { data, onAction })
    }[route];
    return /* @__PURE__ */ React.createElement("div", { style: { display: "flex", height: "100vh", overflow: "hidden" } }, /* @__PURE__ */ React.createElement(Sidebar, { active: route, onNav: setRoute }), /* @__PURE__ */ React.createElement("div", { style: { flex: 1, display: "flex", flexDirection: "column", minWidth: 0 } }, /* @__PURE__ */ React.createElement(Topbar, { crumbs: TITLES[route], role, onRole: setRole, onRefresh: refresh, refreshing }), data.alert && alertOpen && /* @__PURE__ */ React.createElement(AlertBar, { severity: data.alert.severity, count: data.alert.count, message: data.alert.message, actionLabel: data.alert.action, onAction: () => setRoute("diagnostics"), onDismiss: () => setAlertOpen(false) }), /* @__PURE__ */ React.createElement("main", { style: { flex: 1, overflowY: "auto", padding: "20px 24px" } }, /* @__PURE__ */ React.createElement("div", { style: { maxWidth: "var(--content-max)", margin: "0 auto" } }, Screen))), op && /* @__PURE__ */ React.createElement(
      OperationDrawer,
      {
        open: true,
        ...op.preset,
        steps: op.steps,
        running: op.running,
        result: op.result,
        onConfirm: () => runOp(op.preset),
        onClose: () => setOp(null)
      }
    ), confirm && /* @__PURE__ */ React.createElement(ConfirmDialog, { open: true, ...confirm, onConfirm: confirmRun, onClose: () => setConfirm(null) }), wizard && /* @__PURE__ */ React.createElement(Wizard, { onClose: () => setWizard(false), onAdded: refresh }), webrtcOpen && /* @__PURE__ */ React.createElement(WebRtcForm, { onClose: () => setWebrtcOpen(false), onSaved: refresh }), streamTune && /* @__PURE__ */ React.createElement(StreamTuneForm, { target: streamTune, onClose: () => setStreamTune(null), onSaved: refresh }));
  }
  ReactDOM.createRoot(document.getElementById("root")).render(/* @__PURE__ */ React.createElement(App, null));
  setTimeout(() => window.lucide && window.lucide.createIcons(), 120);
})();
