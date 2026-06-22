"""UI view-model builder — the `/api/v1/ui/*` contract behind the Gateway
Operator Console (design_system/ ui kit).

The console is a *fleet operations* surface: it speaks nodes / streams / health /
recovery / maintenance, never raw ports or systemctl. This module aggregates the
existing admin state (StreamBinding store + Janus media age + FDIR events + system
mode) into ONE operator-facing view-model whose shape mirrors the kit's mock
`window.FLEET` (ui_kits/operator-console/fleet-data.js). Raw machine states are
passed through verbatim — the client `StatusBadge` owns state→colour mapping
(the canonical 5-family model), so we never invent status colours server-side.

Pure + I/O-injected (rtp-age / events / mode / janus health are callables) so the
builder is unit-testable with no Janus, no FDIR ring, no clock dependency.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from app.services import stream_binding_store as sbs
from app.services import mountpoint_allocator

# Resolved lazily so tests can monkeypatch; mirrors node_provisioner config.
from app.services.node_provisioner import GATEWAY_LAN_IP


# ── small formatters (mirror the kit's relTime / fmtAge) ───────────────

def _rel_time(epoch: Optional[float], now: float) -> str:
    if epoch is None:
        return "—"
    d = max(0, int(round(now - epoch)))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{round(d / 60)}m ago"
    if d < 86400:
        return f"{round(d / 3600)}h ago"
    return f"{round(d / 86400)}d ago"


def _fmt_age(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return f"{ms}ms" if ms < 1000 else f"{round(ms / 100) / 10}s"


def _cidr_of(ip: str) -> str:
    parts = (ip or "").split(".")
    return f"{'.'.join(parts[:3])}.0/24" if len(parts) == 4 else ""


# ── default I/O wiring (overridable for tests) ─────────────────────────

def _default_rtp_age(mp_id: int) -> Optional[int]:
    try:
        from app.services import janus
        v = janus.janus_summary(mp_id).get("video_age_ms")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    except Exception:
        return None


def _default_events(n: int = 30) -> list:
    try:
        from app.services import fdir_events
        return fdir_events.recent(n)
    except Exception:
        return []


def _default_mode() -> str:
    try:
        from app.services import system_mode
        return system_mode.current_mode().value
    except Exception:
        return "unknown"


def _default_janus_ok() -> bool:
    try:
        from app.services import janus
        s = janus.janus_summary()
        return bool(s) and s.get("error") is None
    except Exception:
        return False


def _default_firewall(state_path, alloc_state_path) -> str:
    """Real per-node RTP firewall status from a dry-run diff (NOT a hardcoded
    'synced'): 'synced' when live iptables matches the binding store, 'drift' when
    rules are missing/stale, 'unknown' if the firewall can't be read. The UI must
    not paint green without an actual check (review P0-5)."""
    try:
        from app.services import firewall_sync
        plan = firewall_sync.reconcile(state_path=state_path,
                                       alloc_state_path=alloc_state_path, apply=False)
        return "synced" if plan.is_noop else "drift"
    except Exception:
        return "unknown"


def _default_webrtc() -> list:
    """Non-secret STUN/TURN/WebRTC facts as DiagnosticsPanel rows ({key,value,status}).
    NEVER includes the TURN password / shared secret — only a present/unset status
    and the credential MECHANISM (matches get_client_rtc_config's disclosure rule)."""
    try:
        from app.core.settings import get_settings
        s = get_settings()
    except Exception:
        return [{"key": "webrtc", "value": "settings unavailable", "status": "warn"}]
    try:
        from app.services.nat_config import load_nat_config
        nat = load_nat_config()
    except Exception:
        nat = None

    turn_host = (getattr(nat, "turn_server", "") or getattr(s, "turn_host", "")) if (nat or s) else ""
    shared = bool(getattr(s, "turn_shared_secret", ""))
    static_pw = bool(getattr(s, "turn_pass", "")) or bool(getattr(nat, "turn_pwd", ""))
    has_creds = shared or static_pw
    mechanism = "ephemeral-hmac" if shared else ("static-password" if static_pw else "none")
    ice_policy = "relay" if getattr(s, "camera_type", "") == "depth_camera" else \
        (s.ice_policy if getattr(s, "ice_policy", "all") in ("all", "relay") else "all")
    stun = (f"stun:{nat.stun_server}:{nat.stun_port}"
            if nat and getattr(nat, "stun_server", "") else "unset")

    rows = [
        {"key": "ice_policy", "value": ice_policy, "status": "ok"},
        {"key": "stun_server", "value": stun, "status": "ok" if stun != "unset" else "idle"},
        {"key": "turn_server", "value": turn_host or "unset",
         "status": "ok" if turn_host else "warn"},
        {"key": "turn_transport", "value": getattr(nat, "turn_type", "") or "—"},
        {"key": "turn_username", "value": getattr(nat, "turn_user", "") or getattr(s, "turn_user", "") or "—"},
        {"key": "turn_udp_port", "value": str(getattr(nat, "turn_port", "") or getattr(s, "turn_port", "") or "—")},
        {"key": "turn_tls_port", "value": str(getattr(s, "turn_tls_port", "") or "—")},
        {"key": "credential_mechanism", "value": mechanism,
         "status": "ok" if has_creds else "bad"},
        {"key": "turn_credentials", "value": "present" if has_creds else "UNSET",
         "status": "ok" if has_creds else "bad"},
        {"key": "credential_ttl_s", "value": str(getattr(s, "turn_cred_ttl", "") or "—")},
    ]
    return rows


# ── builder ────────────────────────────────────────────────────────────

def build_fleet(*, state_path=sbs.DEFAULT_STATE_PATH,
                alloc_state_path=mountpoint_allocator.DEFAULT_STATE_PATH,
                rtp_age_fn: Optional[Callable[[int], Optional[int]]] = None,
                events_fn: Optional[Callable[[int], list]] = None,
                mode_fn: Optional[Callable[[], str]] = None,
                janus_ok_fn: Optional[Callable[[], bool]] = None,
                webrtc_fn: Optional[Callable[[], list]] = None,
                firewall_fn: Optional[Callable[[], str]] = None,
                now: Optional[float] = None) -> dict:
    """Aggregate the StreamBinding topology + media age + FDIR events into the
    operator-console view-model (see module docstring)."""
    rtp_age_fn = rtp_age_fn or _default_rtp_age
    events_fn = events_fn or _default_events
    mode_fn = mode_fn or _default_mode
    janus_ok_fn = janus_ok_fn or _default_janus_ok
    webrtc_fn = webrtc_fn or _default_webrtc
    firewall_fn = firewall_fn or (lambda: _default_firewall(state_path, alloc_state_path))
    now = now if now is not None else time.time()

    nodes = sbs.list_nodes(state_path=state_path)
    bindings = sbs.list_bindings(state_path=state_path, alloc_state_path=alloc_state_path)

    # bindings grouped by node, with media age resolved once per binding
    by_node: dict[str, list] = {}
    streams_flat: list[dict] = []
    for b in bindings.values():
        age_ms = rtp_age_fn(b.janus.mountpoint_id)
        row = {
            "binding": b.binding_id,
            "node": b.node_id,
            "sensor": b.sensor,
            "status": b.status,
            "rtpAgeMs": age_ms,
            "mountpoint": b.janus.mountpoint_id,
            "rtpPort": b.transport.rtp_port,
            "fdir": "enabled" if b.fdir.enabled else "disabled",
            "lastError": None,
        }
        streams_flat.append(row)
        by_node.setdefault(b.node_id, []).append({
            "sensor": b.sensor, "status": b.status,
            "mp": b.janus.mountpoint_id, "port": b.transport.rtp_port,
            "rtpAge": _fmt_age(age_ms),
        })

    # the local node row carries no serial (it's implicit) — derive it from its
    # serial-keyed local projections ('{serial}:{sensor}') so the cam10 card is complete.
    local_serial = next((b.binding_id.rsplit(":", 1)[0]
                         for b in bindings.values()
                         if b.node_id == sbs.LOCAL_NODE_ID and ":" in b.binding_id
                         and b.binding_id.rsplit(":", 1)[0] not in ("", sbs.LOCAL_NODE_ID)), None)

    node_views = []
    for n in nodes.values():
        local = n.node_id == sbs.LOCAL_NODE_ID
        serial = (n.serial or local_serial) if local else n.serial
        node_views.append({
            "nodeId": n.node_id,
            "host": n.host,
            "role": "local_gateway" if local else "remote_producer",
            "model": None,                       # not stored; the device serial is the anchor
            "serial": serial,
            "status": "online" if local or n.reachability == "reachable" else (n.reachability or "unknown"),
            "local": local,
            "health": {
                "agent": "online" if local or n.reachability == "reachable" else (n.reachability or "unknown"),
                "camera": "present" if serial else "unknown",
                "lastSeen": "now" if local else _rel_time(n.last_checked_at, now),
                "provision": "ready" if local else (n.provision_state or "unprovisioned"),
                "maintenance": "on" if getattr(n, "maintenance", False) else "off",
                "hostKey": "n/a" if local else ("pinned" if n.host_key else "unset"),
                "token": "n/a" if local else ("present" if n.agent_token else "missing"),
                "lastError": getattr(n, "last_error", None),
            },
            "streams": sorted(by_node.get(n.node_id, []), key=lambda s: s["sensor"]),
        })

    # metrics + alert/attention from the worst stream (status families: bad>warn)
    _BAD = {"failed", "stale", "critical", "stopped", "unreachable"}
    _WARN = {"degraded", "waiting", "waiting_for_rtp", "pending", "drift"}
    live = sum(1 for s in streams_flat if s["status"] == "online")
    total = len(streams_flat)
    nodes_online = sum(1 for nv in node_views if nv["status"] == "online")
    events = events_fn(30)
    bad = [s for s in streams_flat if s["status"] in _BAD]
    warn = [s for s in streams_flat if s["status"] in _WARN]
    worst = (bad or warn or [None])[0]

    alert = None
    attention = None
    if worst is not None:
        sev = "critical" if worst in bad else "warning"
        alert = {"severity": sev, "count": len(bad) + len(warn),
                 "message": f"{worst['binding']} {worst['status']}",
                 "action": "Open diagnostics"}
        attention = {"binding": worst["binding"], "status": worst["status"],
                     "error": worst["lastError"]}

    mode = mode_fn()
    fw = firewall_fn()                          # real dry-run diff: synced/drift/unknown
    services = [
        {"name": "Gateway", "status": "healthy"},
        {"name": "Janus", "status": "healthy" if janus_ok_fn() else "unreachable"},
        {"name": "FDIR", "status": "enabled" if mode in ("normal", "nominal", "unknown") else mode},
        {"name": "Firewall", "status": fw},
        {"name": "Streams", "status": "degraded" if live < total else "healthy",
         "label": f"{live}/{total} live"},
    ]

    event_views = []      # EventTimeline shape (Command Center / Diagnostics overview)
    fdir_views = []       # Diagnostics > FDIR-events table shape (different columns)
    for e in events:
        ts = e.get("timestamp")
        hhmm = time.strftime("%H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
        target = e.get("binding_id") or e.get("node_id") or ""
        outcome = e.get("outcome") or ""
        suppressed = "yes" if (outcome == "suppressed" or (e.get("details") or {}).get("suppressed")) else "no"
        event_views.append({
            "time": hhmm, "target": target,
            "message": e.get("detection_signal") or "",
            "result": outcome, "action": e.get("recovery_action") or "",
            "actor": e.get("domain") or "",
        })
        fdir_views.append({
            "time": hhmm, "binding": target, "domain": e.get("domain") or "",
            "signal": e.get("detection_signal") or "", "action": e.get("recovery_action") or "",
            "result": outcome, "suppressed": suppressed,
            "reason": (e.get("details") or {}).get("reason", ""),
        })

    # security posture rows (DiagnosticsPanel {key,value,status}) — derived from the
    # remote fleet + the firewall backstop range.
    remotes = [nv for nv in node_views if not nv["local"]]
    pinned = sum(1 for nv in remotes if nv["health"]["hostKey"] == "pinned")
    tokened = sum(1 for nv in remotes if nv["health"]["token"] == "present")
    try:
        from app.services.firewall_sync import REMOTE_RTP_RANGE
    except Exception:
        REMOTE_RTP_RANGE = "—"
    security = [
        {"key": "admin_api", "value": "token-gated", "status": "ok"},
        {"key": "firewall_backstop", "value": REMOTE_RTP_RANGE, "status": "ok"},
        {"key": "host_keys_pinned", "value": f"{pinned}/{len(remotes)}",
         "status": "ok" if pinned == len(remotes) else "warn"},
        {"key": "node_tokens", "value": f"{tokened}/{len(remotes)}",
         "status": "ok" if tokened == len(remotes) else "warn"},
    ]

    return {
        "gateway": {"lanIp": GATEWAY_LAN_IP, "cidr": _cidr_of(GATEWAY_LAN_IP)},
        "services": services,
        "metrics": {
            "nodesOnline": [nodes_online, len(node_views)],
            "streamsLive": [live, total],
            "fdirEvents": len(events),
            "openAlerts": (1 if alert else 0),
        },
        "alert": alert,
        "attention": attention,
        "nodes": node_views,
        "streams": streams_flat,
        "events": event_views,
        "fdirEvents": fdir_views,
        "security": security,
        "webrtc": webrtc_fn(),
    }
