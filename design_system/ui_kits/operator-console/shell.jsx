// Console shell — sidebar nav, topbar, global alert bar, routing.
// Exposes window.ConsoleShell, window.Sidebar, window.Topbar.
const NS = window.GatewayConsoleDesignSystem_64aa70;
const { AlertBar } = NS;

const NAV = [
  { id: "command", label: "Command Center", icon: "layout-dashboard" },
  { id: "fleet", label: "Fleet", icon: "git-compare-arrows" },
  { id: "nodes", label: "Nodes", icon: "server" },
  { id: "streams", label: "Streams", icon: "layers" },
  { id: "viewer", label: "Viewer Wall", icon: "monitor-play" },
  { id: "diagnostics", label: "Diagnostics", icon: "stethoscope" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const BRAND = "GatewayConsoleDesignSystem_64aa70";

function Sidebar({ active, onNav }) {
  return (
    <aside style={{ width: "var(--sidebar-w)", flex: "none", background: "var(--surface-chrome)", display: "flex", flexDirection: "column", borderRight: "1px solid var(--surface-chrome-2)" }}>
      {/* brand */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 16px 14px", borderBottom: "1px solid var(--border-chrome)" }}>
        <span style={{ width: 30, height: 30, borderRadius: "var(--radius-md)", background: "var(--blue-600)", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
          <i data-lucide="cctv" style={{ width: 18, height: 18, color: "#fff" }} />
        </span>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span style={{ font: "var(--weight-bold) var(--text-sm)/1 var(--font-sans)", color: "#fff", letterSpacing: "0.02em" }}>GATEWAY</span>
          <span style={{ font: "var(--weight-medium) var(--text-2xs)/1 var(--font-sans)", color: "var(--slate-400)", letterSpacing: "0.18em", marginTop: 3 }}>CONSOLE</span>
        </div>
      </div>
      {/* nav */}
      <nav style={{ flex: 1, padding: "10px 8px", display: "flex", flexDirection: "column", gap: 2 }}>
        {NAV.map((n) => {
          const on = active === n.id;
          return (
            <button key={n.id} onClick={() => onNav(n.id)}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", borderRadius: "var(--radius-sm)", border: "none", cursor: "pointer", textAlign: "left",
                background: on ? "var(--blue-600)" : "transparent",
                color: on ? "#fff" : "var(--slate-300)",
                font: `${on ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-base)/1 var(--font-sans)`,
                transition: "background 120ms" }}
              onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = "rgba(255,255,255,0.06)"; }}
              onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = "transparent"; }}>
              <i data-lucide={n.icon} style={{ width: 17, height: 17, flex: "none" }} />
              {n.label}
            </button>
          );
        })}
      </nav>
      {/* footer */}
      <div style={{ padding: "12px 14px", borderTop: "1px solid var(--border-chrome)", display: "flex", alignItems: "center", gap: 9 }}>
        <span style={{ width: 8, height: 8, borderRadius: "999px", background: "var(--status-ok-solid)", flex: "none" }} />
        <span style={{ font: "var(--type-mono)", fontSize: "var(--text-2xs)", color: "var(--slate-400)" }}>gateway 192.168.1.10</span>
      </div>
    </aside>
  );
}

const ROLES = ["Operator", "Engineer", "Admin"];

function Topbar({ crumbs, role, onRole, onRefresh, refreshing }) {
  return (
    <header style={{ height: "var(--topbar-h)", flex: "none", background: "var(--surface-card)", borderBottom: "1px solid var(--border-subtle)", display: "flex", alignItems: "center", gap: 14, padding: "0 18px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, flex: 1 }}>
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <i data-lucide="chevron-right" style={{ width: 14, height: 14, color: "var(--text-faint)" }} />}
            <span style={{ font: i === crumbs.length - 1 ? "var(--weight-semibold) var(--text-md)/1 var(--font-sans)" : "var(--weight-medium) var(--text-base)/1 var(--font-sans)", color: i === crumbs.length - 1 ? "var(--text-strong)" : "var(--text-muted)" }}>{c}</span>
          </React.Fragment>
        ))}
      </div>
      {/* role switcher */}
      <div style={{ display: "flex", background: "var(--surface-sunken)", borderRadius: "var(--radius-sm)", padding: 2 }}>
        {ROLES.map((r) => (
          <button key={r} onClick={() => onRole(r)}
            style={{ padding: "5px 11px", border: "none", borderRadius: "var(--radius-xs)", cursor: "pointer",
              background: role === r ? "var(--surface-card)" : "transparent",
              boxShadow: role === r ? "var(--shadow-sm)" : "none",
              color: role === r ? "var(--text-strong)" : "var(--text-muted)",
              font: `${role === r ? "var(--weight-semibold)" : "var(--weight-medium)"} var(--text-sm)/1 var(--font-sans)` }}>
            {r}
          </button>
        ))}
      </div>
      <button onClick={onRefresh} aria-label="Refresh"
        style={{ width: 32, height: 32, display: "flex", alignItems: "center", justifyContent: "center", border: "1px solid var(--border-default)", borderRadius: "var(--radius-sm)", background: "var(--surface-card)", cursor: "pointer", color: "var(--text-muted)" }}>
        <i data-lucide="refresh-cw" style={{ width: 15, height: 15, animation: refreshing ? "gc-spin 0.8s linear infinite" : "none" }} />
      </button>
    </header>
  );
}

window.Sidebar = Sidebar;
window.Topbar = Topbar;
