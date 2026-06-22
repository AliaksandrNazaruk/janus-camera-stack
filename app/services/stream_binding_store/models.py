"""StreamBinding domain models — value objects, enums, the binding-invariant error, and the
implicit local-node sentinel. Pure data: NO file IO, flock, allocator, or secrets. This is the
leaf of the stream_binding_store package (Phase 13A, D2) — everything else imports from here, it
imports nothing from the package. Moved verbatim from the original stream_binding_store module;
the facade (__init__) re-exports every name so `sbs.StreamMode/StreamBinding/NodeEntry/…` is
unchanged for all callers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

LOOPBACK = "127.0.0.1"
LOCAL_NODE_ID = "cam10"


class BindingValidationError(ValueError):
    """A binding violates a model invariant (see STREAM_BINDING_MODEL §3)."""


class StreamMode(str, Enum):
    LOCAL_PRODUCER = "local_producer"
    REMOTE_PRODUCER = "remote_producer"


class StreamStatus(str, Enum):
    """A binding's STORED status — its last-known / intent projection, NOT a live probe.

    Desired-vs-actual model (Cycle 12 — see docs/CONTRACT.md "State model: desired vs actual"):
      * DESIRED intent  = the allocator's ``desired_active`` flag (operator's choice; the boot
        reconciler's source of truth). A LOCAL binding's status derives from it (online iff active).
      * STORED status   = this field — last-known for remote (via ``set_status``), an intent
        projection for local. It can be STALE vs reality; never treat it as the live truth.
      * ACTUAL liveness = probed on demand: ``janus.janus_summary`` (RTP age), ``reachability``;
        the read-only desired-vs-actual report is ``reconcile_drift`` + ``ui_viewmodel``.
    Act on ``desired_active`` for intent and on the probes / ``reconcile_drift`` for actual; use
    ``status`` for display + the last-known cache, not as the truth for what is live.
    """
    CONFIGURED_OFFLINE = "configured_offline"
    WAITING_FOR_RTP = "waiting_for_rtp"
    ONLINE = "online"
    STALE = "stale"
    DEGRADED = "degraded"


# ── value objects ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class StreamTransport:
    rtp_port: int
    payload_type: int = 96
    codec: str = "h264"
    srtp: Optional[dict] = None          # SRTP params placeholder (GATEWAY §4.1)

    def to_dict(self) -> dict:
        return {
            "rtp_port": self.rtp_port,
            "payload_type": self.payload_type,
            "codec": self.codec,
            "srtp": self.srtp,
        }

    @classmethod
    def from_raw(cls, raw: dict) -> "StreamTransport":
        return cls(
            rtp_port=int(raw["rtp_port"]),
            payload_type=int(raw.get("payload_type", 96)),
            codec=str(raw.get("codec", "h264")),
            srtp=raw.get("srtp"),
        )


@dataclass(frozen=True)
class StreamJanusConfig:
    mountpoint_id: int
    rtp_iface: str

    def to_dict(self) -> dict:
        return {"mountpoint_id": self.mountpoint_id, "rtp_iface": self.rtp_iface}

    @classmethod
    def from_raw(cls, raw: dict) -> "StreamJanusConfig":
        return cls(mountpoint_id=int(raw["mountpoint_id"]), rtp_iface=str(raw["rtp_iface"]))


@dataclass(frozen=True)
class StreamFdirConfig:
    enabled: bool = True
    policy: str = "stream_default"       # within-mode tuning; NOT the safety cap

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "policy": self.policy}

    @classmethod
    def from_raw(cls, raw: dict) -> "StreamFdirConfig":
        return cls(
            enabled=bool(raw.get("enabled", True)),
            policy=str(raw.get("policy", "stream_default")),
        )


@dataclass(frozen=True)
class NodeEntry:
    node_id: str                          # opaque, gateway-minted (never the IP or a typed label)
    host: str                             # mutable network locator (IPv4)
    role: str
    reachability: str = "unknown"
    ordinal: Optional[int] = None         # remote allocation-window index
    serial: Optional[str] = None          # camera device serial (librealsense) — set after probe
    display_name: Optional[str] = None    # operator-facing label; never a key
    provision_state: Optional[str] = None # added/reachable/probing/no_camera/deploying/bound/waiting_for_rtp/online/failed
    host_key: Optional[str] = None        # pinned SSH host key (known_hosts line), captured at enrollment
    agent_token: Optional[str] = None     # per-node node-agent bearer token (minted at enrollment); never logged
    maintenance: bool = False             # operator-set pause: FDIR skips this node's bindings (no false recovery while servicing hw)
    last_error: Optional[str] = None      # last provision/op failure detail (operator diagnostics; cleared on success)
    last_checked_at: Optional[float] = None  # epoch secs of the last reachability probe (for "last seen" in the UI)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "role": self.role,
            "reachability": self.reachability,
            "ordinal": self.ordinal,
            "serial": self.serial,
            "display_name": self.display_name,
            "provision_state": self.provision_state,
            "host_key": self.host_key,
            "maintenance": self.maintenance,
            "last_error": self.last_error,
            "last_checked_at": self.last_checked_at,
            # agent_token is NOT serialised here — it lives in the 0600
            # node_secrets.json (review H3). This file is non-secret topology.
        }

    @classmethod
    def from_raw(cls, node_id: str, raw: dict) -> "NodeEntry":
        return cls(
            node_id=node_id,
            host=str(raw["host"]),
            role=str(raw.get("role", "")),
            reachability=str(raw.get("reachability", "unknown")),
            ordinal=raw.get("ordinal"),
            serial=raw.get("serial"),
            display_name=raw.get("display_name"),
            provision_state=raw.get("provision_state"),
            host_key=raw.get("host_key"),
            maintenance=bool(raw.get("maintenance", False)),
            last_error=raw.get("last_error"),
            last_checked_at=raw.get("last_checked_at"),
            # agent_token overlaid from the secret store by get_node/list_nodes.
        )


@dataclass(frozen=True)
class StreamBinding:
    binding_id: str
    node_id: str
    sensor: str
    mode: StreamMode
    transport: StreamTransport
    janus: StreamJanusConfig
    fdir: StreamFdirConfig = StreamFdirConfig()
    status: str = StreamStatus.CONFIGURED_OFFLINE.value
    # Operator Start/Stop intent (desired UP/down), SEPARATE from fdir.enabled (auto-recovery).
    # The node-lifecycle contract (local + remote) is docs/NODE_CONTRACT.md.
    # Unified-node-lifecycle: the gateway ensures a Janus mountpoint for every desired_up binding,
    # and a node-reconciler brings up exactly the desired set — same contract local + remote.
    # Historically Stop was conflated with fdir.enabled; a legacy row without this field DERIVES it
    # from fdir.enabled so behaviour is byte-for-byte unchanged until the desired_up gates land.
    desired_up: bool = True

    def to_dict(self) -> dict:
        return {
            "binding_id": self.binding_id,
            "node_id": self.node_id,
            "sensor": self.sensor,
            "mode": self.mode.value,
            "transport": self.transport.to_dict(),
            "janus": self.janus.to_dict(),
            "fdir": self.fdir.to_dict(),
            "status": self.status,
            "desired_up": self.desired_up,
        }

    @classmethod
    def from_raw(cls, raw: dict) -> "StreamBinding":
        # `mode` is REQUIRED and never defaulted — a stale row must not silently
        # become local_producer (that would run local recovery on a remote fault).
        fdir = StreamFdirConfig.from_raw(raw.get("fdir", {}))
        return cls(
            binding_id=str(raw["binding_id"]),
            node_id=str(raw["node_id"]),
            sensor=str(raw["sensor"]),
            mode=StreamMode(raw["mode"]),
            transport=StreamTransport.from_raw(raw["transport"]),
            janus=StreamJanusConfig.from_raw(raw["janus"]),
            fdir=fdir,
            status=str(raw.get("status", StreamStatus.CONFIGURED_OFFLINE.value)),
            # back-compat: legacy rows had no desired_up → derive from the old Stop flag.
            desired_up=bool(raw.get("desired_up", fdir.enabled)),
        )


LOCAL_NODE = NodeEntry(node_id=LOCAL_NODE_ID, host=LOOPBACK,
                       role="gateway_camera", reachability="local")
