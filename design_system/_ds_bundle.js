/* @ds-bundle: {"format":3,"namespace":"GatewayConsoleDesignSystem_64aa70","components":[{"name":"ActionButton","sourcePath":"components/core/ActionButton.jsx"},{"name":"MetricStat","sourcePath":"components/core/MetricStat.jsx"},{"name":"StatusBadge","sourcePath":"components/core/StatusBadge.jsx"},{"name":"DiagnosticsPanel","sourcePath":"components/data/DiagnosticsPanel.jsx"},{"name":"DriftDiff","sourcePath":"components/data/DriftDiff.jsx"},{"name":"EventTimeline","sourcePath":"components/data/EventTimeline.jsx"},{"name":"HealthCard","sourcePath":"components/data/HealthCard.jsx"},{"name":"NodeCard","sourcePath":"components/data/NodeCard.jsx"},{"name":"StreamRow","sourcePath":"components/data/StreamRow.jsx"},{"name":"ViewerTile","sourcePath":"components/data/ViewerTile.jsx"},{"name":"AlertBar","sourcePath":"components/feedback/AlertBar.jsx"},{"name":"ConfirmDialog","sourcePath":"components/overlay/ConfirmDialog.jsx"},{"name":"OperationDrawer","sourcePath":"components/overlay/OperationDrawer.jsx"}],"sourceHashes":{"components/core/ActionButton.jsx":"336d5b2cc64b","components/core/MetricStat.jsx":"71413461650a","components/core/StatusBadge.jsx":"7d52cf34bff9","components/data/DiagnosticsPanel.jsx":"f415c9bcd1f2","components/data/DriftDiff.jsx":"014e711813b1","components/data/EventTimeline.jsx":"a70d783ee761","components/data/HealthCard.jsx":"9d204e8fcbb8","components/data/NodeCard.jsx":"f8e34164e4bb","components/data/StreamRow.jsx":"d7bca9938da9","components/data/ViewerTile.jsx":"eca0397654db","components/feedback/AlertBar.jsx":"82ea49486279","components/overlay/ConfirmDialog.jsx":"ce1a17cb2217","components/overlay/OperationDrawer.jsx":"6c4db688cdca","ui_kits/operator-console/fleet-data.js":"4484c9be63ef","ui_kits/operator-console/screens.jsx":"ba02975ca669","ui_kits/operator-console/shell.jsx":"ff847ffb54cd"},"inlinedExternals":[],"unexposedExports":[{"name":"statusFamily","sourcePath":"components/core/StatusBadge.jsx"}]} */

(() => {

const __ds_ns = (window.GatewayConsoleDesignSystem_64aa70 = window.GatewayConsoleDesignSystem_64aa70 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/core/ActionButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Variant → visual treatment. The four action classes from the spec map onto
 * these: Class A/B safe & reversible use "default"/"ghost"; Class A primary
 * path uses "primary"; Class C service-impacting uses "warning"; Class D
 * destructive uses "danger". */
const VARIANTS = {
  primary: {
    bg: "var(--action)",
    color: "var(--action-text)",
    border: "var(--blue-700)",
    hoverBg: "var(--action-hover)"
  },
  default: {
    bg: "var(--surface-card)",
    color: "var(--text-body)",
    border: "var(--border-default)",
    hoverBg: "var(--surface-hover)"
  },
  ghost: {
    bg: "transparent",
    color: "var(--text-muted)",
    border: "transparent",
    hoverBg: "var(--surface-hover)"
  },
  warning: {
    bg: "var(--amber-50)",
    color: "var(--amber-800)",
    border: "var(--status-warn-border)",
    hoverBg: "var(--amber-100)"
  },
  danger: {
    bg: "var(--red-50)",
    color: "var(--red-700)",
    border: "var(--status-bad-border)",
    hoverBg: "var(--red-100)"
  },
  "danger-solid": {
    bg: "var(--red-600)",
    color: "var(--white)",
    border: "var(--red-700)",
    hoverBg: "var(--red-700)"
  }
};
const SIZES = {
  xs: {
    pad: "2px 8px",
    font: "var(--text-xs)",
    h: 24,
    gap: 4,
    icon: 13
  },
  sm: {
    pad: "4px 10px",
    font: "var(--text-sm)",
    h: 28,
    gap: 5,
    icon: 14
  },
  md: {
    pad: "6px 14px",
    font: "var(--text-base)",
    h: 34,
    gap: 6,
    icon: 16
  },
  lg: {
    pad: "9px 18px",
    font: "var(--text-md)",
    h: 40,
    gap: 7,
    icon: 18
  }
};

/**
 * ActionButton — the console's button primitive. One control covers every
 * action class via `variant`; pass a Lucide icon name to `icon` to get the
 * leading glyph (requires the Lucide script on the page).
 */
function ActionButton({
  children,
  variant = "default",
  size = "md",
  icon,
  iconRight,
  disabled = false,
  busy = false,
  block = false,
  style,
  ...rest
}) {
  const v = VARIANTS[variant] || VARIANTS.default;
  const s = SIZES[size] || SIZES.md;
  const [hover, setHover] = React.useState(false);
  const Icon = name => name ? /*#__PURE__*/React.createElement("i", {
    "data-lucide": name,
    style: {
      width: s.icon,
      height: s.icon,
      flex: "none"
    }
  }) : null;
  return /*#__PURE__*/React.createElement("button", _extends({}, rest, {
    disabled: disabled || busy,
    onMouseEnter: e => {
      setHover(true);
      rest.onMouseEnter && rest.onMouseEnter(e);
    },
    onMouseLeave: e => {
      setHover(false);
      rest.onMouseLeave && rest.onMouseLeave(e);
    },
    style: {
      display: block ? "flex" : "inline-flex",
      width: block ? "100%" : undefined,
      alignItems: "center",
      justifyContent: "center",
      gap: s.gap,
      minHeight: s.h,
      padding: s.pad,
      borderRadius: "var(--radius-sm)",
      border: `1px solid ${v.border}`,
      background: hover && !disabled ? v.hoverBg : v.bg,
      color: v.color,
      font: `var(--weight-semibold) ${s.font}/1 var(--font-sans)`,
      cursor: disabled ? "not-allowed" : busy ? "wait" : "pointer",
      opacity: disabled ? 0.5 : 1,
      transition: "background 120ms ease, border-color 120ms ease",
      whiteSpace: "nowrap",
      ...style
    }
  }), busy ? /*#__PURE__*/React.createElement("i", {
    "data-lucide": "loader-circle",
    style: {
      width: s.icon,
      height: s.icon,
      animation: "gc-spin 0.8s linear infinite"
    }
  }) : Icon(icon), children, Icon(iconRight));
}
Object.assign(__ds_scope, { ActionButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/ActionButton.jsx", error: String((e && e.message) || e) }); }

// components/core/MetricStat.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * MetricStat — a single Command-Center counter (Nodes Online, Streams Live,
 * FDIR Events, Open Alerts). Big mono numeral over an uppercase label, with an
 * optional status accent and trend/subtext line.
 */
function MetricStat({
  label,
  value,
  total,
  family = "idle",
  hint,
  icon,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderRadius: "var(--radius-lg)",
      padding: "14px 16px",
      display: "flex",
      flexDirection: "column",
      gap: 6,
      position: "relative",
      overflow: "hidden",
      ...style
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      left: 0,
      top: 0,
      bottom: 0,
      width: "var(--border-accent)",
      background: `var(--status-${family}-solid)`
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)"
    }
  }, label), icon && /*#__PURE__*/React.createElement("i", {
    "data-lucide": icon,
    style: {
      width: 15,
      height: 15,
      color: "var(--text-faint)"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-bold) var(--text-3xl)/1 var(--font-mono)",
      letterSpacing: "var(--tracking-tight)",
      color: `var(--status-${family}-fg)`
    }
  }, value), total != null && /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-medium) var(--text-lg)/1 var(--font-mono)",
      color: "var(--text-faint)"
    }
  }, "/", total)), hint && /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-body)",
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, hint));
}
Object.assign(__ds_scope, { MetricStat });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/MetricStat.jsx", error: String((e && e.message) || e) }); }

// components/core/StatusBadge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Canonical mapping: every node / stream / FDIR / firewall state string the
 * gateway emits resolves to exactly one of five families. Unknown strings
 * fall back to "idle" so the UI never renders an uncolored status. */
const FAMILY_BY_STATE = {
  // ok · green
  online: "ok",
  synced: "ok",
  healthy: "ok",
  ready: "ok",
  active: "ok",
  enabled: "ok",
  present: "ok",
  valid: "ok",
  reachable: "ok",
  pinned: "ok",
  ok: "ok",
  connected: "ok",
  running: "ok",
  // warn · amber
  degraded: "warn",
  waiting: "warn",
  waiting_for_rtp: "warn",
  warning: "warn",
  drift: "warn",
  pending: "warn",
  rotated: "warn",
  unsigned: "warn",
  provision_required: "warn",
  // bad · red
  failed: "bad",
  stale: "bad",
  critical: "bad",
  stopped: "bad",
  unreachable: "bad",
  blocked: "bad",
  apply_failed: "bad",
  error: "bad",
  missing: "bad",
  down: "bad",
  // idle · gray
  offline: "idle",
  disabled: "idle",
  unknown: "idle",
  configured_offline: "idle",
  off: "idle",
  unset: "idle",
  absent: "idle",
  idle: "idle",
  removed: "idle",
  // busy · blue
  provisioning: "busy",
  recovering: "busy",
  maintenance: "busy",
  host_key_pending: "busy",
  registered: "busy",
  removing: "busy",
  suppressed: "busy"
};

/** Resolve a raw state string to its status family. */
function statusFamily(state) {
  if (!state) return "idle";
  return FAMILY_BY_STATE[String(state).toLowerCase()] || "idle";
}
const SIZES = {
  sm: {
    pad: "1px 7px",
    font: "var(--text-2xs)",
    dot: 6,
    gap: 4
  },
  md: {
    pad: "2px 9px",
    font: "var(--text-xs)",
    dot: 8,
    gap: 5
  },
  lg: {
    pad: "4px 12px",
    font: "var(--text-sm)",
    dot: 9,
    gap: 6
  }
};

/**
 * StatusBadge — the single status primitive for the console. Pass a raw state
 * string (e.g. "waiting_for_rtp"); it auto-maps to a family color and shows a
 * dot + label. Override the family with `family` when you need a fixed color.
 */
function StatusBadge({
  state,
  label,
  family,
  size = "md",
  dot = true,
  pulse = false,
  style,
  ...rest
}) {
  const fam = family || statusFamily(state);
  const s = SIZES[size] || SIZES.md;
  const text = label != null ? label : String(state || "unknown").replace(/_/g, " ");
  return /*#__PURE__*/React.createElement("span", _extends({}, rest, {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: s.gap,
      padding: s.pad,
      borderRadius: "var(--radius-pill)",
      background: `var(--status-${fam}-bg)`,
      color: `var(--status-${fam}-fg)`,
      border: `1px solid var(--status-${fam}-border)`,
      font: `var(--weight-semibold) ${s.font}/1 var(--font-sans)`,
      whiteSpace: "nowrap",
      textTransform: "lowercase",
      letterSpacing: "0.01em",
      ...style
    }
  }), dot && /*#__PURE__*/React.createElement("span", {
    style: {
      width: s.dot,
      height: s.dot,
      borderRadius: "var(--radius-pill)",
      background: `var(--status-${fam}-solid)`,
      flex: "none",
      boxShadow: pulse ? `0 0 0 0 var(--status-${fam}-solid)` : "none",
      animation: pulse ? "gc-pulse 1.6s ease-out infinite" : "none"
    }
  }), text);
}
Object.assign(__ds_scope, { statusFamily, StatusBadge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/StatusBadge.jsx", error: String((e && e.message) || e) }); }

// components/data/DiagnosticsPanel.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * DiagnosticsPanel — a titled block of key/value diagnostic rows (the Agent /
 * Camera / Services / Data-plane / Control-plane sections of Diagnostics).
 * Values render in mono; pass `status` on a row to color it as a state.
 */
function DiagnosticsPanel({
  title,
  icon,
  rows = [],
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderRadius: "var(--radius-lg)",
      overflow: "hidden",
      ...style
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "10px 14px",
      borderBottom: "1px solid var(--border-subtle)",
      background: "var(--surface-sunken)"
    }
  }, icon && /*#__PURE__*/React.createElement("i", {
    "data-lucide": icon,
    style: {
      width: 15,
      height: 15,
      color: "var(--text-muted)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-label)",
      fontSize: "var(--text-xs)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)"
    }
  }, title)), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "6px 14px"
    }
  }, rows.map((r, i) => {
    const fam = r.status ? __ds_scope.statusFamily(r.status) : null;
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      style: {
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "7px 0",
        borderBottom: i < rows.length - 1 ? "1px solid var(--slate-100)" : "none"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--type-mono)",
        color: "var(--text-muted)",
        width: 150,
        flex: "none"
      }
    }, r.key), fam ? /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        font: "var(--type-mono-strong)",
        color: `var(--status-${fam}-fg)`
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 7,
        height: 7,
        borderRadius: "var(--radius-pill)",
        background: `var(--status-${fam}-solid)`
      }
    }), r.value) : /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--type-mono-strong)",
        color: "var(--text-strong)"
      }
    }, r.value));
  })));
}
Object.assign(__ds_scope, { DiagnosticsPanel });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DiagnosticsPanel.jsx", error: String((e && e.message) || e) }); }

// components/data/DriftDiff.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const Col = ({
  title,
  tone,
  children
}) => /*#__PURE__*/React.createElement("div", {
  style: {
    flex: 1,
    minWidth: 0
  }
}, /*#__PURE__*/React.createElement("div", {
  style: {
    font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)",
    textTransform: "uppercase",
    letterSpacing: "var(--tracking-label)",
    color: tone || "var(--text-muted)",
    marginBottom: 8
  }
}, title), /*#__PURE__*/React.createElement("div", {
  style: {
    display: "flex",
    flexDirection: "column",
    gap: 4
  }
}, children));
const Line = ({
  children,
  strike,
  tone
}) => /*#__PURE__*/React.createElement("div", {
  style: {
    font: "var(--type-mono)",
    color: tone || "var(--text-body)",
    textDecoration: strike ? "line-through" : "none",
    opacity: strike ? 0.6 : 1,
    padding: "3px 8px",
    borderRadius: "var(--radius-xs)",
    background: "var(--surface-sunken)"
  }
}, children);

/**
 * DriftDiff — desired vs actual reconcile view for the Fleet page. Three
 * columns (Desired · Actual · Drift); drift rows are highlighted amber so the
 * operator sees exactly what reconcile would change before applying.
 */
function DriftDiff({
  desired = [],
  actual = [],
  drift = [],
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderRadius: "var(--radius-lg)",
      padding: "16px 18px",
      display: "flex",
      gap: 20,
      ...style
    }
  }), /*#__PURE__*/React.createElement(Col, {
    title: "Desired"
  }, desired.map((d, i) => /*#__PURE__*/React.createElement(Line, {
    key: i
  }, d))), /*#__PURE__*/React.createElement(Col, {
    title: "Actual"
  }, actual.map((a, i) => /*#__PURE__*/React.createElement(Line, {
    key: i,
    tone: a.startsWith("!") ? "var(--status-warn-fg)" : undefined
  }, a.replace(/^!/, "")))), /*#__PURE__*/React.createElement(Col, {
    title: "Drift",
    tone: drift.length ? "var(--status-warn-fg)" : "var(--status-ok-fg)"
  }, drift.length ? drift.map((d, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      font: "var(--type-mono)",
      color: "var(--status-warn-fg)",
      padding: "3px 8px",
      borderRadius: "var(--radius-xs)",
      background: "var(--amber-50)",
      border: "1px solid var(--status-warn-border)"
    }
  }, d)) : /*#__PURE__*/React.createElement(Line, {
    tone: "var(--status-ok-fg)"
  }, "no drift \xB7 in sync")));
}
Object.assign(__ds_scope, { DriftDiff });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DriftDiff.jsx", error: String((e && e.message) || e) }); }

// components/data/EventTimeline.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * EventTimeline — chronological FDIR / audit / activity feed. Each entry has a
 * mono timestamp, a status-colored node, a message, and optional actor/target
 * mono metadata. Used in Diagnostics, the Command-Center event list, and node
 * / stream Activity tabs.
 */
function EventTimeline({
  events = [],
  dense = false,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      display: "flex",
      flexDirection: "column",
      ...style
    }
  }), events.map((e, i) => {
    const fam = e.family || __ds_scope.statusFamily(e.result || e.level || "info");
    const last = i === events.length - 1;
    return /*#__PURE__*/React.createElement("div", {
      key: e.id || i,
      style: {
        display: "flex",
        gap: 12,
        alignItems: "stretch"
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        width: 10,
        flex: "none"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 9,
        height: 9,
        borderRadius: "var(--radius-pill)",
        background: `var(--status-${fam}-solid)`,
        marginTop: dense ? 6 : 8,
        flex: "none",
        boxShadow: "0 0 0 3px var(--surface-card)"
      }
    }), !last && /*#__PURE__*/React.createElement("span", {
      style: {
        width: 2,
        flex: 1,
        background: "var(--slate-200)"
      }
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        paddingBottom: last ? 0 : dense ? 10 : 14,
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 2
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "baseline",
        gap: 8,
        flexWrap: "wrap"
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--type-mono)",
        color: "var(--text-faint)"
      }
    }, e.time), e.target && /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--type-mono-strong)",
        color: `var(--status-${fam}-fg)`
      }
    }, e.target), /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--weight-regular) var(--text-base)/1.4 var(--font-sans)",
        color: "var(--text-body)"
      }
    }, e.message)), (e.actor || e.action) && /*#__PURE__*/React.createElement("span", {
      style: {
        font: "var(--type-mono)",
        color: "var(--text-faint)",
        fontSize: "var(--text-2xs)"
      }
    }, e.action ? e.action : "", e.actor ? ` · by ${e.actor}` : "", e.reason ? ` · ${e.reason}` : "")));
  }));
}
Object.assign(__ds_scope, { EventTimeline });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/EventTimeline.jsx", error: String((e && e.message) || e) }); }

// components/data/HealthCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * HealthCard — the Command-Center "System Status" strip. A title plus a row of
 * service health pills (Gateway · Janus · Cloudflare · FDIR · Firewall …).
 * The card's left accent reflects the worst service state so a single glance
 * answers "is everything healthy?".
 */
function HealthCard({
  title = "System Status",
  services = [],
  style,
  ...rest
}) {
  const order = {
    bad: 0,
    warn: 1,
    busy: 2,
    idle: 3,
    ok: 4
  };
  const worst = services.reduce((acc, s) => {
    const f = __ds_scope.statusFamily(s.status);
    return order[f] < order[acc] ? f : acc;
  }, "ok");
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderLeft: `var(--border-accent) solid var(--status-${worst}-solid)`,
      borderRadius: "var(--radius-lg)",
      padding: "13px 16px",
      display: "flex",
      alignItems: "center",
      gap: 16,
      flexWrap: "wrap",
      ...style
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": worst === "ok" ? "shield-check" : "shield-alert",
    style: {
      width: 18,
      height: 18,
      color: `var(--status-${worst}-solid)`
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-card-title)",
      color: "var(--text-strong)"
    }
  }, title)), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 1,
      alignSelf: "stretch",
      background: "var(--border-subtle)"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 18,
      flexWrap: "wrap",
      alignItems: "center"
    }
  }, services.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.name,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 7
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-medium) var(--text-sm)/1 var(--font-sans)",
      color: "var(--text-muted)"
    }
  }, s.name), /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    state: s.status,
    label: s.label,
    size: "sm"
  })))));
}
Object.assign(__ds_scope, { HealthCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/HealthCard.jsx", error: String((e && e.message) || e) }); }

// components/data/NodeCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const HealthItem = ({
  label,
  value,
  family
}) => /*#__PURE__*/React.createElement("div", {
  style: {
    display: "flex",
    flexDirection: "column",
    gap: 3
  }
}, /*#__PURE__*/React.createElement("span", {
  style: {
    font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
    textTransform: "uppercase",
    letterSpacing: "var(--tracking-label)",
    color: "var(--text-faint)"
  }
}, label), family ? /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
  family: family,
  label: value,
  size: "sm"
}) : /*#__PURE__*/React.createElement("span", {
  style: {
    font: "var(--type-mono)",
    color: "var(--text-body)"
  }
}, value));

/**
 * NodeCard — a physical camera node (local gateway `.10` or remote `.55`).
 * Identical layout for both; only the left accent differs (blue = local,
 * slate = remote). Shows the health grid, per-sensor stream summary, the safe
 * + operational action row, and a separated Danger Zone toggle.
 */
function NodeCard({
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
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderLeft: `var(--border-accent) solid ${local ? "var(--blue-600)" : "var(--slate-400)"}`,
      borderRadius: "var(--radius-lg)",
      padding: "16px 18px",
      display: "flex",
      flexDirection: "column",
      gap: 14,
      ...style
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: 12,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 4,
      flex: 1,
      minWidth: 180
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 9
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-bold) var(--text-xl)/1 var(--font-mono)",
      color: "var(--text-strong)"
    }
  }, nodeId), /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    state: status,
    pulse: status === "provisioning" || status === "recovering"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: local ? "var(--blue-700)" : "var(--text-muted)",
      background: local ? "var(--blue-50)" : "var(--surface-sunken)",
      padding: "3px 7px",
      borderRadius: "var(--radius-xs)"
    }
  }, local ? "local" : "remote")), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      color: "var(--text-muted)"
    }
  }, host, " \xB7 ", role.replace(/_/g, " "), model ? ` · ${model}` : "", serial ? ` · serial ${serial}` : "")), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "ghost",
    icon: "stethoscope",
    onClick: onCheck
  }, "Check")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))",
      gap: 12,
      padding: "12px 0",
      borderTop: "1px solid var(--slate-100)",
      borderBottom: "1px solid var(--slate-100)"
    }
  }, /*#__PURE__*/React.createElement(HealthItem, {
    label: "Agent",
    value: health.agent || "online",
    family: __ds_scope.statusFamily(health.agent || "online")
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Camera",
    value: health.camera || "present",
    family: __ds_scope.statusFamily(health.camera || "present")
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Last seen",
    value: health.lastSeen || "8s ago"
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Provision",
    value: health.provision || "ready",
    family: __ds_scope.statusFamily(health.provision || "ready")
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Maintenance",
    value: health.maintenance || "off",
    family: health.maintenance === "on" ? "busy" : "idle"
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Host key",
    value: health.hostKey || "pinned",
    family: health.hostKey === "pinned" ? "ok" : "warn"
  }), /*#__PURE__*/React.createElement(HealthItem, {
    label: "Token",
    value: health.token || "present",
    family: __ds_scope.statusFamily(health.token === "present" ? "valid" : health.token)
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, streams.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.sensor,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      font: "var(--type-mono)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-strong)",
      fontWeight: 500,
      width: 48
    }
  }, s.sensor), /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    state: s.status,
    size: "sm"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-faint)",
      marginLeft: "auto"
    }
  }, "mp ", s.mp, " \xB7 port ", s.port, " \xB7 rtp ", s.rtpAge)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 6,
      flexWrap: "wrap",
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "primary",
    icon: "layers",
    onClick: onOpenStreams
  }, "Streams"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "default",
    icon: "download",
    onClick: onProvision
  }, "Provision"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "default",
    icon: "wrench",
    onClick: onMaintenance
  }, "Maintenance"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "default",
    icon: "key-round",
    onClick: onRotate
  }, "Rotate token"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "sm",
    variant: "ghost",
    icon: danger ? "chevron-up" : "chevron-down",
    style: {
      marginLeft: "auto"
    },
    onClick: () => setDanger(d => !d)
  }, "Danger Zone")), danger && /*#__PURE__*/React.createElement("div", {
    style: {
      background: "var(--red-50)",
      border: "1px solid var(--status-bad-border)",
      borderRadius: "var(--radius-md)",
      padding: "11px 13px",
      display: "flex",
      gap: 6,
      flexWrap: "wrap",
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-semibold) var(--text-xs)/1 var(--font-sans)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--red-700)",
      marginRight: 4
    }
  }, "Danger Zone"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "danger",
    icon: "trash-2",
    onClick: onRemove
  }, "Remove node"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "danger",
    icon: "power"
  }, "Deprovision"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "danger",
    icon: "key-square"
  }, "Forget host key")));
}
Object.assign(__ds_scope, { NodeCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/NodeCard.jsx", error: String((e && e.message) || e) }); }

// components/data/StreamRow.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const Mono = ({
  children,
  muted,
  style
}) => /*#__PURE__*/React.createElement("span", {
  style: {
    font: "var(--type-mono)",
    color: muted ? "var(--text-faint)" : "var(--text-body)",
    ...style
  }
}, children);

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
function StreamRow({
  binding,
  // "cam55:color"
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
  const cell = {
    padding: "9px 12px",
    verticalAlign: "middle",
    borderBottom: "1px solid var(--slate-100)"
  };
  return /*#__PURE__*/React.createElement("tr", _extends({}, rest, {
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      background: selected ? "var(--blue-50)" : hover ? "var(--surface-hover)" : "transparent",
      ...style
    }
  }), /*#__PURE__*/React.createElement("td", {
    style: {
      ...cell,
      paddingLeft: 14
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono-strong)",
      color: "var(--text-strong)"
    }
  }, binding || `${node}:${sensor}`)), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(Mono, null, node)), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(Mono, {
    muted: true
  }, sensor)), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    state: status,
    size: "sm"
  })), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono-strong)",
      color: `var(--status-${ageFamily(rtpAgeMs)}-fg)`
    }
  }, fmtAge(rtpAgeMs))), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(Mono, {
    muted: true
  }, mountpoint ?? "—")), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(Mono, {
    muted: true
  }, rtpPort ?? "—")), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    family: fdir === "enabled" ? "ok" : fdir === "suppressed" ? "busy" : "idle",
    label: fdir === "enabled" ? "on" : fdir === "suppressed" ? "supp" : "off",
    size: "sm",
    dot: false
  })), /*#__PURE__*/React.createElement("td", {
    style: cell
  }, lastError ? /*#__PURE__*/React.createElement(Mono, {
    style: {
      color: "var(--status-bad-fg)"
    }
  }, lastError) : /*#__PURE__*/React.createElement(Mono, {
    muted: true
  }, "\u2014")), /*#__PURE__*/React.createElement("td", {
    style: {
      ...cell,
      paddingRight: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 4,
      justifyContent: "flex-end"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "external-link",
    onClick: onOpen,
    "aria-label": "Open viewer"
  }), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "default",
    icon: "rotate-cw",
    onClick: onRestart
  }, "Restart"), status === "online" || status === "stale" || status === "degraded" ? /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "warning",
    onClick: onStop
  }, "Stop") : /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "stethoscope",
    onClick: onDiagnose
  }, "Diagnose"))));
}
Object.assign(__ds_scope, { StreamRow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/StreamRow.jsx", error: String((e && e.message) || e) }); }

// components/data/ViewerTile.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * ViewerTile — one tile of the Viewer Wall. A 16:9 video surface with a status
 * overlay (binding id · state · rtp_age), an optional last-FDIR-event badge,
 * a pin toggle, and a per-tile quick-action footer. Use a real <video> via the
 * `media` slot; otherwise a placeholder frame renders.
 */
function ViewerTile({
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
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      background: "var(--slate-950)",
      border: `1px solid ${pinned ? "var(--blue-600)" : "var(--border-default)"}`,
      borderRadius: "var(--radius-lg)",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      ...style
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "8px 10px",
      background: "linear-gradient(180deg, rgba(2,6,23,0.85), rgba(2,6,23,0))",
      position: "relative",
      zIndex: 2
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono-strong)",
      color: "var(--white)"
    }
  }, binding), /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    state: status,
    size: "sm"
  }), rtpAge != null && /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      color: "rgba(255,255,255,0.7)"
    }
  }, "rtp ", rtpAge), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: pinned ? "pin" : "pin-off",
    onClick: onPin,
    style: {
      marginLeft: "auto",
      color: pinned ? "var(--blue-400)" : "rgba(255,255,255,0.6)"
    },
    "aria-label": "Pin"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      aspectRatio: "16 / 9",
      background: "#000",
      marginTop: -40,
      display: "flex",
      alignItems: "center",
      justifyContent: "center"
    }
  }, media || /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 6,
      color: "rgba(255,255,255,0.25)"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": "video",
    style: {
      width: 30,
      height: 30
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      fontSize: "var(--text-2xs)"
    }
  }, binding)), fdirEvent && /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      left: 10,
      bottom: 10,
      font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
      color: "var(--white)",
      background: "var(--status-busy-solid)",
      padding: "3px 7px",
      borderRadius: "var(--radius-xs)",
      display: "inline-flex",
      alignItems: "center",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": "activity",
    style: {
      width: 11,
      height: 11
    }
  }), "FDIR ", fdirEvent)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 6,
      padding: "8px 10px",
      background: "var(--slate-900)",
      borderTop: "1px solid var(--border-chrome)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "default",
    icon: "rotate-cw",
    onClick: onRestart,
    style: {
      background: "var(--slate-800)",
      borderColor: "var(--slate-700)",
      color: "var(--slate-100)"
    }
  }, "Restart"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "stethoscope",
    onClick: onDiagnose,
    style: {
      color: "var(--slate-300)"
    }
  }, "Diag"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "maximize-2",
    onClick: onFullscreen,
    style: {
      marginLeft: "auto",
      color: "var(--slate-300)"
    },
    "aria-label": "Fullscreen"
  })));
}
Object.assign(__ds_scope, { ViewerTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/ViewerTile.jsx", error: String((e && e.message) || e) }); }

// components/feedback/AlertBar.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const SEV = {
  critical: {
    bg: "var(--alert-critical-bg)",
    fg: "var(--alert-critical-fg)",
    accent: "var(--alert-critical-accent)",
    icon: "octagon-alert"
  },
  warning: {
    bg: "var(--alert-warning-bg)",
    fg: "var(--alert-warning-fg)",
    accent: "var(--alert-warning-accent)",
    icon: "triangle-alert"
  },
  info: {
    bg: "var(--alert-info-bg)",
    fg: "var(--alert-info-fg)",
    accent: "var(--alert-info-accent)",
    icon: "info"
  }
};

/**
 * AlertBar — the global alert strip pinned under the topbar. Shows the highest
 * open severity, a count, the lead message, and an optional action. Alerts are
 * grouped by severity (critical / warning / info); pass the worst one here.
 */
function AlertBar({
  severity = "info",
  message,
  count,
  actionLabel,
  onAction,
  onDismiss,
  style,
  ...rest
}) {
  const s = SEV[severity] || SEV.info;
  return /*#__PURE__*/React.createElement("div", _extends({}, rest, {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      minHeight: "var(--alertbar-h)",
      padding: "0 14px",
      background: s.bg,
      borderBottom: `1px solid ${s.accent}`,
      boxShadow: `inset var(--border-accent) 0 0 0 ${s.accent}`,
      ...style
    }
  }), /*#__PURE__*/React.createElement("i", {
    "data-lucide": s.icon,
    style: {
      width: 16,
      height: 16,
      color: s.accent,
      flex: "none"
    }
  }), count != null && /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-bold) var(--text-2xs)/1 var(--font-sans)",
      color: "var(--white)",
      background: s.accent,
      padding: "2px 7px",
      borderRadius: "var(--radius-pill)"
    }
  }, count), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-medium) var(--text-base)/1.3 var(--font-sans)",
      color: s.fg,
      flex: 1
    }
  }, message), actionLabel && /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    onClick: onAction,
    style: {
      color: s.fg
    }
  }, actionLabel), onDismiss && /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "x",
    onClick: onDismiss,
    style: {
      color: s.fg
    },
    "aria-label": "Dismiss"
  }));
}
Object.assign(__ds_scope, { AlertBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/AlertBar.jsx", error: String((e && e.message) || e) }); }

// components/overlay/ConfirmDialog.jsx
try { (() => {
/**
 * ConfirmDialog — modal for Class C (service-impacting) and Class D
 * (destructive) actions. Class C shows an impact list; Class D additionally
 * requires the operator to type an exact phrase before the confirm button
 * unlocks, and lays out what will be removed vs kept plus the rollback path.
 */
function ConfirmDialog({
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
  onClose
}) {
  const [typed, setTyped] = React.useState("");
  React.useEffect(() => {
    if (open) setTyped("");
  }, [open]);
  if (!open) return null;
  const locked = confirmPhrase && typed.trim() !== confirmPhrase;
  const accent = destructive ? "bad" : "warn";
  const List = ({
    items,
    icon,
    color
  }) => /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      paddingLeft: 0,
      listStyle: "none",
      display: "flex",
      flexDirection: "column",
      gap: 5
    }
  }, items.map((it, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    style: {
      display: "flex",
      gap: 7,
      alignItems: "baseline",
      font: "var(--type-mono)",
      color: color || "var(--text-body)"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": icon,
    style: {
      width: 13,
      height: 13,
      flex: "none",
      position: "relative",
      top: 2
    }
  }), it)));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      inset: 0,
      zIndex: 220,
      display: "flex",
      alignItems: "flex-start",
      justifyContent: "center",
      paddingTop: "9vh"
    }
  }, /*#__PURE__*/React.createElement("div", {
    onClick: onClose,
    style: {
      position: "absolute",
      inset: 0,
      background: "var(--surface-overlay)"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      width: 480,
      maxWidth: "92vw",
      background: "var(--surface-card)",
      borderRadius: "var(--radius-xl)",
      boxShadow: "var(--shadow-overlay)",
      overflow: "hidden",
      animation: "gc-pop 160ms ease"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "16px 18px",
      borderBottom: "1px solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 30,
      height: 30,
      borderRadius: "var(--radius-md)",
      background: `var(--status-${accent}-bg)`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flex: "none"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": destructive ? "octagon-alert" : "triangle-alert",
    style: {
      width: 17,
      height: 17,
      color: `var(--status-${accent}-solid)`
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-section)",
      color: "var(--text-strong)"
    }
  }, title)), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "16px 18px",
      display: "flex",
      flexDirection: "column",
      gap: 14
    }
  }, message && /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      font: "var(--type-body)",
      color: "var(--text-body)"
    }
  }, message), impact.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 7
    }
  }, "Impact"), /*#__PURE__*/React.createElement(List, {
    items: impact,
    icon: "dot"
  })), willRemove.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--status-bad-fg)",
      marginBottom: 7
    }
  }, "Will be removed"), /*#__PURE__*/React.createElement(List, {
    items: willRemove,
    icon: "minus",
    color: "var(--status-bad-fg)"
  })), willKeep.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--status-ok-fg)",
      marginBottom: 7
    }
  }, "Will stay"), /*#__PURE__*/React.createElement(List, {
    items: willKeep,
    icon: "check",
    color: "var(--status-ok-fg)"
  })), rollback && /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-body)",
      color: "var(--text-muted)"
    }
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--text-body)"
    }
  }, "Rollback:"), " ", rollback), confirmPhrase && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-body)",
      color: "var(--text-body)"
    }
  }, "Type ", /*#__PURE__*/React.createElement("code", {
    style: {
      font: "var(--type-mono-strong)",
      color: "var(--status-bad-fg)",
      background: "var(--surface-sunken)",
      padding: "1px 6px",
      borderRadius: "var(--radius-xs)"
    }
  }, confirmPhrase), " to confirm"), /*#__PURE__*/React.createElement("input", {
    autoFocus: true,
    value: typed,
    onChange: e => setTyped(e.target.value),
    placeholder: confirmPhrase,
    style: {
      font: "var(--type-mono)",
      padding: "8px 10px",
      borderRadius: "var(--radius-sm)",
      border: `1px solid ${locked ? "var(--border-default)" : "var(--status-ok-solid)"}`,
      outline: "none",
      color: "var(--text-strong)"
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      padding: "14px 18px",
      borderTop: "1px solid var(--border-subtle)",
      justifyContent: "flex-end"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    variant: "ghost",
    onClick: onClose
  }, "Cancel"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    variant: destructive ? "danger-solid" : "warning",
    disabled: locked,
    onClick: onConfirm
  }, confirmLabel))));
}
Object.assign(__ds_scope, { ConfirmDialog });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/overlay/ConfirmDialog.jsx", error: String((e && e.message) || e) }); }

// components/overlay/OperationDrawer.jsx
try { (() => {
const StepRow = ({
  label,
  state
}) => {
  const fam = state === "ok" ? "ok" : state === "active" ? "busy" : state === "failed" ? "bad" : "idle";
  const icon = state === "ok" ? "check" : state === "failed" ? "x" : state === "active" ? "loader-circle" : "circle";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "7px 0"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": icon,
    style: {
      width: 15,
      height: 15,
      color: `var(--status-${fam}-solid)`,
      animation: state === "active" ? "gc-spin 0.8s linear infinite" : "none"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-body)",
      color: state === "pending" ? "var(--text-faint)" : "var(--text-body)"
    }
  }, label));
};

/**
 * OperationDrawer — every mutation opens this right-side drawer. Pre-execution
 * it states the action, its impact, the FDIR consequence and expected
 * duration; on confirm it streams step progress. Embodies the spec's
 * dry-run → impact → confirm → verify flow for Class B/C operations.
 */
function OperationDrawer({
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
  onClose
}) {
  if (!open) return null;
  const classColor = {
    A: "ok",
    B: "busy",
    C: "warn",
    D: "bad"
  }[impactClass] || "busy";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      inset: 0,
      zIndex: 200,
      display: "flex",
      justifyContent: "flex-end"
    }
  }, /*#__PURE__*/React.createElement("div", {
    onClick: onClose,
    style: {
      position: "absolute",
      inset: 0,
      background: "var(--surface-overlay)",
      backdropFilter: "blur(1px)"
    }
  }), /*#__PURE__*/React.createElement("aside", {
    style: {
      position: "relative",
      width: "var(--drawer-w)",
      maxWidth: "92vw",
      height: "100%",
      background: "var(--surface-card)",
      boxShadow: "var(--shadow-overlay)",
      display: "flex",
      flexDirection: "column",
      animation: "gc-slidein 180ms ease"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: 10,
      padding: "16px 18px",
      borderBottom: "1px solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: "flex",
      flexDirection: "column",
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-section)",
      color: "var(--text-strong)"
    }
  }, title), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-bold) var(--text-2xs)/1 var(--font-sans)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: `var(--status-${classColor}-fg)`,
      background: `var(--status-${classColor}-bg)`,
      border: `1px solid var(--status-${classColor}-border)`,
      padding: "3px 7px",
      borderRadius: "var(--radius-xs)"
    }
  }, "Class ", impactClass)), target && /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      color: "var(--text-muted)"
    }
  }, target)), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    size: "xs",
    variant: "ghost",
    icon: "x",
    onClick: onClose,
    "aria-label": "Close"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: "auto",
      padding: "16px 18px",
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, !result && /*#__PURE__*/React.createElement(React.Fragment, null, impact.length > 0 && /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 8
    }
  }, "Impact"), /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      paddingLeft: 18,
      display: "flex",
      flexDirection: "column",
      gap: 5
    }
  }, impact.map((it, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    style: {
      font: "var(--type-body)",
      color: "var(--text-body)"
    }
  }, it)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 22,
      flexWrap: "wrap"
    }
  }, fdirNote && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 6
    }
  }, "FDIR"), /*#__PURE__*/React.createElement(__ds_scope.StatusBadge, {
    family: fdirNote.includes("disabled") ? "idle" : "ok",
    label: fdirNote,
    size: "sm"
  })), duration && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 6
    }
  }, "Expected duration"), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono-strong)",
      color: "var(--text-strong)"
    }
  }, duration)))), steps && /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 4
    }
  }, "Progress"), /*#__PURE__*/React.createElement("div", null, steps.map((s, i) => /*#__PURE__*/React.createElement(StepRow, {
    key: i,
    label: s.label,
    state: s.state
  })))), result && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "11px 13px",
      borderRadius: "var(--radius-md)",
      background: result === "ok" ? "var(--status-ok-bg)" : "var(--status-bad-bg)",
      color: result === "ok" ? "var(--status-ok-fg)" : "var(--status-bad-fg)",
      font: "var(--weight-semibold) var(--text-md)/1 var(--font-sans)"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": result === "ok" ? "circle-check-big" : "circle-x",
    style: {
      width: 18,
      height: 18
    }
  }), result === "ok" ? "Operation completed" : "Operation failed")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      padding: "14px 18px",
      borderTop: "1px solid var(--border-subtle)",
      justifyContent: "flex-end"
    }
  }, result ? /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    variant: "primary",
    onClick: onClose
  }, "Done") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    variant: "ghost",
    onClick: onClose
  }, "Cancel"), /*#__PURE__*/React.createElement(__ds_scope.ActionButton, {
    variant: impactClass === "C" ? "warning" : "primary",
    busy: running,
    onClick: onConfirm
  }, confirmLabel)))));
}
Object.assign(__ds_scope, { OperationDrawer });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/overlay/OperationDrawer.jsx", error: String((e && e.message) || e) }); }

// ui_kits/operator-console/fleet-data.js
try { (() => {
// Mock fleet view-model — mirrors the shape of /api/v1/ui/* endpoints in the
// spec. Used by the Operator Console UI kit screens. Exposed on window.
window.FLEET = {
  gateway: {
    lanIp: "192.168.1.10",
    cidr: "192.168.1.0/24"
  },
  services: [{
    name: "Gateway",
    status: "healthy"
  }, {
    name: "Janus",
    status: "healthy"
  }, {
    name: "Cloudflare",
    status: "healthy"
  }, {
    name: "FDIR",
    status: "enabled"
  }, {
    name: "Firewall",
    status: "synced"
  }, {
    name: "Streams",
    status: "degraded",
    label: "3/4 live"
  }],
  metrics: {
    nodesOnline: [2, 2],
    streamsLive: [3, 4],
    fdirEvents: 1,
    openAlerts: 1
  },
  alert: {
    severity: "warning",
    count: 1,
    message: "cam55/depth waiting_for_rtp — no packets received in 24s",
    action: "Open diagnostics"
  },
  attention: {
    binding: "cam55:depth",
    status: "waiting_for_rtp",
    error: "no packets received · rs-stream@depth failed on node"
  },
  nodes: [{
    nodeId: "cam10",
    host: "192.168.1.10",
    role: "local_gateway",
    model: "RealSense D435i",
    serial: "938422071421",
    status: "online",
    local: true,
    health: {
      agent: "online",
      camera: "present",
      lastSeen: "now",
      provision: "ready",
      maintenance: "off",
      hostKey: "pinned",
      token: "present"
    },
    streams: [{
      sensor: "color",
      status: "online",
      mp: 1305,
      port: 5004,
      rtpAge: "80ms"
    }, {
      sensor: "depth",
      status: "online",
      mp: 1306,
      port: 5006,
      rtpAge: "85ms"
    }]
  }, {
    nodeId: "cam55",
    host: "192.168.1.55",
    role: "remote_producer",
    model: "RealSense D435",
    serial: "141722072135",
    status: "online",
    local: false,
    health: {
      agent: "online",
      camera: "present",
      lastSeen: "8s ago",
      provision: "ready",
      maintenance: "off",
      hostKey: "pinned",
      token: "present"
    },
    streams: [{
      sensor: "color",
      status: "online",
      mp: 2000,
      port: 5100,
      rtpAge: "90ms"
    }, {
      sensor: "depth",
      status: "stale",
      mp: 2001,
      port: 5102,
      rtpAge: "24s"
    }]
  }],
  streams: [{
    binding: "cam10:color",
    node: "cam10",
    sensor: "color",
    status: "online",
    rtpAgeMs: 80,
    mountpoint: 1305,
    rtpPort: 5004,
    fdir: "enabled",
    lastError: null
  }, {
    binding: "cam10:depth",
    node: "cam10",
    sensor: "depth",
    status: "online",
    rtpAgeMs: 85,
    mountpoint: 1306,
    rtpPort: 5006,
    fdir: "enabled",
    lastError: null
  }, {
    binding: "cam55:color",
    node: "cam55",
    sensor: "color",
    status: "online",
    rtpAgeMs: 110,
    mountpoint: 2000,
    rtpPort: 5100,
    fdir: "enabled",
    lastError: null
  }, {
    binding: "cam55:depth",
    node: "cam55",
    sensor: "depth",
    status: "stale",
    rtpAgeMs: 24000,
    mountpoint: 2001,
    rtpPort: 5102,
    fdir: "disabled",
    lastError: "no RTP"
  }],
  events: [{
    time: "14:22",
    target: "cam55:color",
    message: "restarted by operator",
    result: "ok",
    action: "stream.restart",
    actor: "operator"
  }, {
    time: "14:20",
    target: "cam55",
    message: "FDIR skipped — maintenance was on",
    result: "suppressed",
    reason: "maintenance"
  }, {
    time: "14:15",
    message: "firewall reconcile applied",
    result: "ok",
    action: "firewall.apply",
    actor: "admin"
  }, {
    time: "14:02",
    target: "cam55:depth",
    message: "rs-stream@depth failed on node",
    result: "failed",
    reason: "no_rtp"
  }, {
    time: "13:58",
    target: "cam10:color",
    message: "node checked",
    result: "ok",
    action: "node.check",
    actor: "operator"
  }],
  fdirEvents: [{
    time: "14:22",
    binding: "cam55:color",
    domain: "PRODUCER",
    signal: "rtp_age=25000ms",
    action: "restart_stream",
    result: "ok",
    suppressed: "no",
    reason: "—"
  }, {
    time: "14:24",
    binding: "cam55:depth",
    domain: "PRODUCER",
    signal: "stale",
    action: "skipped",
    result: "—",
    suppressed: "yes",
    reason: "maintenance"
  }],
  security: [{
    key: "Admin auth",
    value: "configured",
    status: "ok"
  }, {
    key: "Viewer auth",
    value: "missing",
    status: "warn"
  }, {
    key: "Node tokens",
    value: "present",
    status: "ok"
  }, {
    key: "Host keys",
    value: "pinned",
    status: "ok"
  }, {
    key: "Secrets perms",
    value: "ok (600)",
    status: "ok"
  }]
};
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/operator-console/fleet-data.js", error: String((e && e.message) || e) }); }

// ui_kits/operator-console/screens.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
// Operator Console screens — composes design-system components into the 7
// sections from the spec. Exposes window.SCREENS keyed by nav id.
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

/* ── shared layout bits ─────────────────────────────────────────────────── */
function Panel({
  title,
  action,
  children,
  pad = true,
  style
}) {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--border-subtle)",
      borderRadius: "var(--radius-lg)",
      ...style
    }
  }, title && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "12px 16px",
      borderBottom: "1px solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      font: "var(--type-card-title)",
      color: "var(--text-strong)"
    }
  }, title), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: "auto",
      display: "flex",
      gap: 6
    }
  }, action)), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: pad ? 16 : 0
    }
  }, children));
}
const StreamsTable = ({
  rows,
  onAction
}) => /*#__PURE__*/React.createElement("table", {
  style: {
    width: "100%",
    borderCollapse: "collapse"
  }
}, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, ["Stream", "Node", "Sensor", "Status", "RTP Age", "MP", "Port", "FDIR", "Last Error"].map(h => /*#__PURE__*/React.createElement("th", {
  key: h,
  style: {
    textAlign: "left",
    font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
    textTransform: "uppercase",
    letterSpacing: "var(--tracking-label)",
    color: "var(--text-faint)",
    padding: "0 12px 9px",
    borderBottom: "1px solid var(--border-subtle)"
  }
}, h)), /*#__PURE__*/React.createElement("th", {
  style: {
    textAlign: "right",
    font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
    textTransform: "uppercase",
    letterSpacing: "var(--tracking-label)",
    color: "var(--text-faint)",
    padding: "0 14px 9px",
    borderBottom: "1px solid var(--border-subtle)"
  }
}, "Actions"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(s => /*#__PURE__*/React.createElement(StreamRow, _extends({
  key: s.binding
}, s, {
  onOpen: () => onAction("open", s),
  onRestart: () => onAction("restart", s),
  onStop: () => onAction("stop", s),
  onDiagnose: () => onAction("diagnose", s)
})))));

/* ── 1 · Command Center ─────────────────────────────────────────────────── */
function CommandCenter({
  data,
  onAction
}) {
  const m = data.metrics;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(HealthCard, {
    services: data.services
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(4, 1fr)",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(MetricStat, {
    label: "Nodes Online",
    value: m.nodesOnline[0],
    total: m.nodesOnline[1],
    family: "ok",
    icon: "server"
  }), /*#__PURE__*/React.createElement(MetricStat, {
    label: "Streams Live",
    value: m.streamsLive[0],
    total: m.streamsLive[1],
    family: "warn",
    icon: "video",
    hint: "cam55/depth waiting"
  }), /*#__PURE__*/React.createElement(MetricStat, {
    label: "FDIR Events",
    value: m.fdirEvents,
    family: "busy",
    icon: "activity",
    hint: "last hour"
  }), /*#__PURE__*/React.createElement(MetricStat, {
    label: "Open Alerts",
    value: m.openAlerts,
    family: "warn",
    icon: "bell",
    hint: "1 warning"
  })), /*#__PURE__*/React.createElement("section", {
    style: {
      background: "var(--surface-card)",
      border: "1px solid var(--status-warn-border)",
      borderLeft: "var(--border-accent) solid var(--status-warn-solid)",
      borderRadius: "var(--radius-lg)",
      padding: "14px 16px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": "triangle-alert",
    style: {
      width: 16,
      height: 16,
      color: "var(--status-warn-solid)"
    }
  }), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      font: "var(--type-card-title)",
      color: "var(--text-strong)"
    }
  }, "Attention Required")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono-strong)",
      color: "var(--text-strong)"
    }
  }, data.attention.binding), /*#__PURE__*/React.createElement(StatusBadge, {
    state: data.attention.status,
    size: "sm"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-body)",
      color: "var(--text-muted)"
    }
  }, "last error: ", data.attention.error), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 6,
      marginLeft: "auto"
    }
  }, /*#__PURE__*/React.createElement(ActionButton, {
    size: "sm",
    variant: "ghost",
    icon: "stethoscope",
    onClick: () => onAction("diagnose", {
      binding: data.attention.binding
    })
  }, "Diagnostics"), /*#__PURE__*/React.createElement(ActionButton, {
    size: "sm",
    variant: "default",
    icon: "rotate-cw",
    onClick: () => onAction("restart", {
      binding: data.attention.binding
    })
  }, "Restart"), /*#__PURE__*/React.createElement(ActionButton, {
    size: "sm",
    variant: "default",
    icon: "wrench",
    onClick: () => onAction("maintenance", {
      binding: data.attention.binding
    })
  }, "Maintenance")))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1.4fr 1fr",
      gap: 16,
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Live Streams",
    pad: false,
    style: {
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "14px 4px 6px"
    }
  }, /*#__PURE__*/React.createElement(StreamsTable, {
    rows: data.streams,
    onAction: onAction
  }))), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent events"
  }, /*#__PURE__*/React.createElement(EventTimeline, {
    dense: true,
    events: data.events.slice(0, 4)
  }))));
}

/* ── 2 · Fleet ──────────────────────────────────────────────────────────── */
function FleetScreen({
  data
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Fleet state \u2014 desired \u2194 actual",
    action: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "ghost",
      icon: "play"
    }, "Dry-run reconcile"), /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "primary",
      icon: "git-merge"
    }, "Apply reconcile"), /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "default",
      icon: "download"
    }, "Export plan"))
  }, /*#__PURE__*/React.createElement(DriftDiff, {
    desired: ["cam10/color enabled", "cam10/depth enabled", "cam55/color enabled", "cam55/depth enabled"],
    actual: ["cam10/color online", "cam10/depth online", "cam55/color online", "!cam55/depth waiting_for_rtp"],
    drift: ["cam55/depth  desired=active  actual=waiting_for_rtp"],
    style: {
      border: "none",
      padding: 0
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Desired \u2014 fleet plan"
  }, /*#__PURE__*/React.createElement("pre", {
    style: {
      margin: 0,
      font: "var(--type-mono)",
      color: "var(--text-body)",
      whiteSpace: "pre-wrap"
    }
  }, `cam10  local_gateway
  color  enabled
  depth  enabled

cam55  remote_producer
  color  enabled
  depth  enabled`)), /*#__PURE__*/React.createElement(Panel, {
    title: "Reconcile flow"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6,
      flexWrap: "wrap",
      font: "var(--weight-semibold) var(--text-sm)/1 var(--font-sans)"
    }
  }, ["dry-run", "diff", "confirm", "apply", "verify", "audit"].map((s, i, a) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: s
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      padding: "6px 11px",
      borderRadius: "var(--radius-pill)",
      background: "var(--surface-sunken)",
      color: "var(--text-body)"
    }
  }, s), i < a.length - 1 && /*#__PURE__*/React.createElement("i", {
    "data-lucide": "arrow-right",
    style: {
      width: 14,
      height: 14,
      color: "var(--text-faint)"
    }
  })))), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 14,
      font: "var(--type-body)",
      color: "var(--text-muted)"
    }
  }, "Every apply-action follows this sequence. Nothing mutates without a diff and confirmation; every step lands in the audit log."))));
}

/* ── 3 · Nodes ──────────────────────────────────────────────────────────── */
function NodesScreen({
  data,
  onAction,
  onAddNode
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      font: "var(--type-body)",
      color: "var(--text-muted)"
    }
  }, "Local and remote nodes are managed the same way."), /*#__PURE__*/React.createElement(ActionButton, {
    variant: "primary",
    icon: "plus",
    style: {
      marginLeft: "auto"
    },
    onClick: onAddNode
  }, "Add node")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, data.nodes.map(n => /*#__PURE__*/React.createElement(NodeCard, _extends({
    key: n.nodeId
  }, n, {
    onCheck: () => onAction("check", n),
    onProvision: () => onAction("provision", n),
    onMaintenance: () => onAction("maintenance", n),
    onRotate: () => onAction("rotate", n),
    onOpenStreams: () => onAction("streams", n),
    onRemove: () => onAction("remove-node", n)
  })))));
}

/* ── 4 · Streams ────────────────────────────────────────────────────────── */
function StreamsScreen({
  data,
  onAction
}) {
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Streams \u2014 all bindings",
    pad: false,
    action: /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "ghost",
      icon: "refresh-cw"
    }, "Refresh \xB7 2s"),
    style: {
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "14px 4px 6px"
    }
  }, /*#__PURE__*/React.createElement(StreamsTable, {
    rows: data.streams,
    onAction: onAction
  })));
}

/* ── 5 · Viewer Wall ────────────────────────────────────────────────────── */
function ViewerWall({
  data
}) {
  const [layout, setLayout] = React.useState("4up");
  const cols = layout === "1up" ? 1 : layout === "2up" ? 2 : 2;
  const shown = layout === "1up" ? data.streams.slice(0, 1) : layout === "2up" ? data.streams.slice(0, 2) : data.streams;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 6
    }
  }, [["1up", "square"], ["2up", "columns-2"], ["4up", "grid-2x2"]].map(([id, icon]) => /*#__PURE__*/React.createElement(ActionButton, {
    key: id,
    size: "sm",
    variant: layout === id ? "primary" : "default",
    icon: icon,
    onClick: () => setLayout(id)
  }, id.replace("up", "-up")))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: `repeat(${cols}, 1fr)`,
      gap: 14
    }
  }, shown.map(s => /*#__PURE__*/React.createElement(ViewerTile, {
    key: s.binding,
    binding: s.binding,
    status: s.status,
    rtpAge: s.status === "stale" ? "24s" : s.rtpAgeMs + "ms",
    pinned: s.binding === "cam55:color",
    fdirEvent: s.binding === "cam55:depth" ? "restart" : null,
    style: {
      minHeight: layout === "1up" ? 420 : "auto"
    }
  }))));
}

/* ── 6 · Diagnostics ────────────────────────────────────────────────────── */
function DiagnosticsScreen({
  data
}) {
  const [tab, setTab] = React.useState("overview");
  const tabs = [["overview", "Overview"], ["node", "Node"], ["stream", "Stream"], ["firewall", "RTP / Firewall"], ["fdir", "FDIR events"], ["audit", "Audit log"]];
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 2,
      borderBottom: "1px solid var(--border-subtle)"
    }
  }, tabs.map(([id, label]) => /*#__PURE__*/React.createElement("button", {
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
  }, label))), tab === "overview" && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Current incidents"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 9
    }
  }, [["cam55/depth stale", "warn"], ["firewall drift detected", "warn"], ["viewer tokens unset", "warn"]].map(([t, f]) => /*#__PURE__*/React.createElement("div", {
    key: t,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 9
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: "999px",
      background: `var(--status-${f}-solid)`
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      color: "var(--text-body)"
    }
  }, t))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent events"
  }, /*#__PURE__*/React.createElement(EventTimeline, {
    dense: true,
    events: data.events
  }))), tab === "node" && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Agent",
    icon: "cpu",
    rows: [{
      key: "reachable",
      value: "yes",
      status: "ok"
    }, {
      key: "version",
      value: "0.1.0"
    }, {
      key: "last_seen",
      value: "8s",
      status: "ok"
    }, {
      key: "token_status",
      value: "valid",
      status: "ok"
    }]
  }), /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Camera",
    icon: "camera",
    rows: [{
      key: "model",
      value: "RealSense D435"
    }, {
      key: "serial",
      value: "141722072135"
    }, {
      key: "usb",
      value: "present",
      status: "ok"
    }, {
      key: "sensors",
      value: "color / depth"
    }]
  }), /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Services",
    icon: "list-checks",
    rows: [{
      key: "node-agent",
      value: "active",
      status: "ok"
    }, {
      key: "realsense-mux",
      value: "active",
      status: "ok"
    }, {
      key: "rs-stream@color",
      value: "active",
      status: "ok"
    }, {
      key: "rs-stream@depth",
      value: "failed",
      status: "failed"
    }]
  }), /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Control plane",
    icon: "sliders-horizontal",
    rows: [{
      key: "fdir",
      value: "enabled",
      status: "ok"
    }, {
      key: "maintenance",
      value: "off"
    }, {
      key: "last_restart",
      value: "14:22"
    }, {
      key: "last_error",
      value: "no_rtp",
      status: "warn"
    }]
  })), tab === "stream" && /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Binding",
    icon: "link",
    rows: [{
      key: "binding_id",
      value: "cam55:color"
    }, {
      key: "mode",
      value: "remote_producer"
    }, {
      key: "rtp_target",
      value: "192.168.1.10:5100"
    }, {
      key: "mountpoint",
      value: "2000"
    }]
  }), /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Data plane",
    icon: "radio",
    rows: [{
      key: "rtp_packets",
      value: "yes",
      status: "ok"
    }, {
      key: "rtp_age_ms",
      value: "90"
    }, {
      key: "janus_video_age",
      value: "100ms"
    }, {
      key: "webrtc_viewers",
      value: "1"
    }]
  })), tab === "firewall" && /*#__PURE__*/React.createElement(Panel, {
    title: "Firewall \u2014 expected \u2194 actual",
    action: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "ghost",
      icon: "play"
    }, "Dry-run"), /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "warning",
      icon: "shield-check"
    }, "Apply"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 20
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 8
    }
  }, "Expected"), /*#__PURE__*/React.createElement("pre", {
    style: {
      margin: 0,
      font: "var(--type-mono)",
      color: "var(--text-body)",
      whiteSpace: "pre-wrap"
    }
  }, `allow udp 192.168.1.55 → :5100
allow udp 192.168.1.55 → :5102`)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      font: "var(--type-label)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-muted)",
      marginBottom: 8
    }
  }, "Actual"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(StatusBadge, {
    family: "ok",
    label: "rule present",
    size: "sm"
  }), /*#__PURE__*/React.createElement(StatusBadge, {
    family: "ok",
    label: "default drop enabled",
    size: "sm"
  }), /*#__PURE__*/React.createElement(StatusBadge, {
    family: "ok",
    label: "janus admin not exposed",
    size: "sm"
  }))))), tab === "fdir" && /*#__PURE__*/React.createElement(Panel, {
    title: "FDIR events",
    pad: false,
    style: {
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("table", {
    style: {
      width: "100%",
      borderCollapse: "collapse"
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, ["Time", "Binding", "Domain", "Signal", "Action", "Result", "Suppressed", "Reason"].map(h => /*#__PURE__*/React.createElement("th", {
    key: h,
    style: {
      textAlign: "left",
      font: "var(--weight-semibold) var(--text-2xs)/1 var(--font-sans)",
      textTransform: "uppercase",
      letterSpacing: "var(--tracking-label)",
      color: "var(--text-faint)",
      padding: "11px 14px",
      borderBottom: "1px solid var(--border-subtle)",
      background: "var(--surface-sunken)"
    }
  }, h)))), /*#__PURE__*/React.createElement("tbody", null, data.fdirEvents.map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    style: {
      borderBottom: "1px solid var(--slate-100)"
    }
  }, /*#__PURE__*/React.createElement("td", {
    style: tdMono
  }, e.time), /*#__PURE__*/React.createElement("td", {
    style: {
      ...tdMono,
      color: "var(--text-strong)"
    }
  }, e.binding), /*#__PURE__*/React.createElement("td", {
    style: tdMono
  }, e.domain), /*#__PURE__*/React.createElement("td", {
    style: tdMono
  }, e.signal), /*#__PURE__*/React.createElement("td", {
    style: td
  }, /*#__PURE__*/React.createElement(StatusBadge, {
    family: e.action === "skipped" ? "idle" : "ok",
    label: e.action,
    size: "sm",
    dot: false
  })), /*#__PURE__*/React.createElement("td", {
    style: td
  }, e.result === "ok" ? /*#__PURE__*/React.createElement(StatusBadge, {
    family: "ok",
    label: "ok",
    size: "sm"
  }) : /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      color: "var(--text-faint)"
    }
  }, e.result)), /*#__PURE__*/React.createElement("td", {
    style: td
  }, /*#__PURE__*/React.createElement(StatusBadge, {
    family: e.suppressed === "yes" ? "busy" : "idle",
    label: e.suppressed,
    size: "sm",
    dot: false
  })), /*#__PURE__*/React.createElement("td", {
    style: {
      ...tdMono,
      color: "var(--text-muted)"
    }
  }, e.reason)))))), tab === "audit" && /*#__PURE__*/React.createElement(Panel, {
    title: "Audit log"
  }, /*#__PURE__*/React.createElement(EventTimeline, {
    events: data.events
  })));
}
const td = {
  padding: "10px 14px",
  verticalAlign: "middle"
};
const tdMono = {
  ...td,
  font: "var(--type-mono)",
  color: "var(--text-body)"
};

/* ── 7 · Settings / Security ────────────────────────────────────────────── */
function SettingsScreen({
  data
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Security",
    icon: "shield",
    rows: data.security
  }), /*#__PURE__*/React.createElement(DiagnosticsPanel, {
    title: "Network",
    icon: "network",
    rows: [{
      key: "gateway_lan_ip",
      value: data.gateway.lanIp
    }, {
      key: "camera_cidr",
      value: data.gateway.cidr
    }, {
      key: "cloudflare",
      value: "connected",
      status: "ok"
    }, {
      key: "rtp_port_pool",
      value: "5000–5200"
    }, {
      key: "firewall",
      value: "synced",
      status: "ok"
    }]
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Runtime config \u2014 live apply",
    action: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "ghost",
      icon: "check"
    }, "Validate"), /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "warning",
      icon: "upload"
    }, "Apply"), /*#__PURE__*/React.createElement(ActionButton, {
      size: "sm",
      variant: "ghost",
      icon: "rotate-ccw"
    }, "Rollback"))
  }, /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      font: "var(--type-body)",
      color: "var(--text-muted)"
    }
  }, "Two-step, confirm-bound: validate a change, then apply the validated revision. No direct edit without validate + impact."), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      display: "flex",
      flexDirection: "column",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(StatusBadge, {
    family: "warn",
    label: "viewer tokens unset",
    size: "sm"
  }), /*#__PURE__*/React.createElement(StatusBadge, {
    family: "ok",
    label: "ice_policy = relay (effective)",
    size: "sm"
  }))));
}
window.SCREENS = {
  CommandCenter,
  FleetScreen,
  NodesScreen,
  StreamsScreen,
  ViewerWall,
  DiagnosticsScreen,
  SettingsScreen
};
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/operator-console/screens.jsx", error: String((e && e.message) || e) }); }

// ui_kits/operator-console/shell.jsx
try { (() => {
// Console shell — sidebar nav, topbar, global alert bar, routing.
// Exposes window.ConsoleShell, window.Sidebar, window.Topbar.
const NS = window.GatewayConsoleDesignSystem_64aa70;
const {
  AlertBar
} = NS;
const NAV = [{
  id: "command",
  label: "Command Center",
  icon: "layout-dashboard"
}, {
  id: "fleet",
  label: "Fleet",
  icon: "git-compare-arrows"
}, {
  id: "nodes",
  label: "Nodes",
  icon: "server"
}, {
  id: "streams",
  label: "Streams",
  icon: "layers"
}, {
  id: "viewer",
  label: "Viewer Wall",
  icon: "monitor-play"
}, {
  id: "diagnostics",
  label: "Diagnostics",
  icon: "stethoscope"
}, {
  id: "settings",
  label: "Settings",
  icon: "settings"
}];
const BRAND = "GatewayConsoleDesignSystem_64aa70";
function Sidebar({
  active,
  onNav
}) {
  return /*#__PURE__*/React.createElement("aside", {
    style: {
      width: "var(--sidebar-w)",
      flex: "none",
      background: "var(--surface-chrome)",
      display: "flex",
      flexDirection: "column",
      borderRight: "1px solid var(--surface-chrome-2)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "16px 16px 14px",
      borderBottom: "1px solid var(--border-chrome)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 30,
      height: 30,
      borderRadius: "var(--radius-md)",
      background: "var(--blue-600)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flex: "none"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": "cctv",
    style: {
      width: 18,
      height: 18,
      color: "#fff"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      lineHeight: 1.1
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-bold) var(--text-sm)/1 var(--font-sans)",
      color: "#fff",
      letterSpacing: "0.02em"
    }
  }, "GATEWAY"), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--weight-medium) var(--text-2xs)/1 var(--font-sans)",
      color: "var(--slate-400)",
      letterSpacing: "0.18em",
      marginTop: 3
    }
  }, "CONSOLE"))), /*#__PURE__*/React.createElement("nav", {
    style: {
      flex: 1,
      padding: "10px 8px",
      display: "flex",
      flexDirection: "column",
      gap: 2
    }
  }, NAV.map(n => {
    const on = active === n.id;
    return /*#__PURE__*/React.createElement("button", {
      key: n.id,
      onClick: () => onNav(n.id),
      style: {
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "9px 10px",
        borderRadius: "var(--radius-sm)",
        border: "none",
        cursor: "pointer",
        textAlign: "left",
        background: on ? "var(--blue-600)" : "transparent",
        color: on ? "#fff" : "var(--slate-300)",
        font: `${on ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-base)/1 var(--font-sans)`,
        transition: "background 120ms"
      },
      onMouseEnter: e => {
        if (!on) e.currentTarget.style.background = "rgba(255,255,255,0.06)";
      },
      onMouseLeave: e => {
        if (!on) e.currentTarget.style.background = "transparent";
      }
    }, /*#__PURE__*/React.createElement("i", {
      "data-lucide": n.icon,
      style: {
        width: 17,
        height: 17,
        flex: "none"
      }
    }), n.label);
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "12px 14px",
      borderTop: "1px solid var(--border-chrome)",
      display: "flex",
      alignItems: "center",
      gap: 9
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 8,
      height: 8,
      borderRadius: "999px",
      background: "var(--status-ok-solid)",
      flex: "none"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: "var(--type-mono)",
      fontSize: "var(--text-2xs)",
      color: "var(--slate-400)"
    }
  }, "gateway 192.168.1.10")));
}
const ROLES = ["Operator", "Engineer", "Admin"];
function Topbar({
  crumbs,
  role,
  onRole,
  onRefresh,
  refreshing
}) {
  return /*#__PURE__*/React.createElement("header", {
    style: {
      height: "var(--topbar-h)",
      flex: "none",
      background: "var(--surface-card)",
      borderBottom: "1px solid var(--border-subtle)",
      display: "flex",
      alignItems: "center",
      gap: 14,
      padding: "0 18px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 7,
      flex: 1
    }
  }, crumbs.map((c, i) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: i
  }, i > 0 && /*#__PURE__*/React.createElement("i", {
    "data-lucide": "chevron-right",
    style: {
      width: 14,
      height: 14,
      color: "var(--text-faint)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      font: i === crumbs.length - 1 ? "var(--weight-semibold) var(--text-md)/1 var(--font-sans)" : "var(--weight-medium) var(--text-base)/1 var(--font-sans)",
      color: i === crumbs.length - 1 ? "var(--text-strong)" : "var(--text-muted)"
    }
  }, c)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      background: "var(--surface-sunken)",
      borderRadius: "var(--radius-sm)",
      padding: 2
    }
  }, ROLES.map(r => /*#__PURE__*/React.createElement("button", {
    key: r,
    onClick: () => onRole(r),
    style: {
      padding: "5px 11px",
      border: "none",
      borderRadius: "var(--radius-xs)",
      cursor: "pointer",
      background: role === r ? "var(--surface-card)" : "transparent",
      boxShadow: role === r ? "var(--shadow-sm)" : "none",
      color: role === r ? "var(--text-strong)" : "var(--text-muted)",
      font: `${role === r ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-sm)/1 var(--font-sans)`
    }
  }, r))), /*#__PURE__*/React.createElement("button", {
    onClick: onRefresh,
    "aria-label": "Refresh",
    style: {
      width: 32,
      height: 32,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      border: "1px solid var(--border-default)",
      borderRadius: "var(--radius-sm)",
      background: "var(--surface-card)",
      cursor: "pointer",
      color: "var(--text-muted)"
    }
  }, /*#__PURE__*/React.createElement("i", {
    "data-lucide": "refresh-cw",
    style: {
      width: 15,
      height: 15,
      animation: refreshing ? "gc-spin 0.8s linear infinite" : "none"
    }
  })));
}
window.Sidebar = Sidebar;
window.Topbar = Topbar;
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/operator-console/shell.jsx", error: String((e && e.message) || e) }); }

__ds_ns.ActionButton = __ds_scope.ActionButton;

__ds_ns.MetricStat = __ds_scope.MetricStat;

__ds_ns.StatusBadge = __ds_scope.StatusBadge;

__ds_ns.DiagnosticsPanel = __ds_scope.DiagnosticsPanel;

__ds_ns.DriftDiff = __ds_scope.DriftDiff;

__ds_ns.EventTimeline = __ds_scope.EventTimeline;

__ds_ns.HealthCard = __ds_scope.HealthCard;

__ds_ns.NodeCard = __ds_scope.NodeCard;

__ds_ns.StreamRow = __ds_scope.StreamRow;

__ds_ns.ViewerTile = __ds_scope.ViewerTile;

__ds_ns.AlertBar = __ds_scope.AlertBar;

__ds_ns.ConfirmDialog = __ds_scope.ConfirmDialog;

__ds_ns.OperationDrawer = __ds_scope.OperationDrawer;

})();
