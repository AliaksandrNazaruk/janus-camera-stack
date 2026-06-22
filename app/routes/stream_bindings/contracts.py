"""Request/response DTOs for the stream_bindings admin API (Cycle 5 split — was inline in the
765-line module). Pure Pydantic models; no package dependency so submodules + __init__ import freely.

GATEWAY_LAN_IP / _SENSOR_RE are captured here for the Field defaults exactly as the original module
captured them at import (a later monkeypatch of stream_bindings.GATEWAY_LAN_IP affects the runtime
add_node read, not these already-bound defaults — unchanged behavior)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services import node_provisioner

GATEWAY_LAN_IP = node_provisioner.GATEWAY_LAN_IP
_SENSOR_RE = r"^(color|depth|ir1|ir2)$"


class NodeOut(BaseModel):
    node_id: str
    host: str
    role: str
    reachability: str
    ordinal: Optional[int] = None
    serial: Optional[str] = None
    display_name: Optional[str] = None
    provision_state: Optional[str] = None
    # Whether an SSH host key is pinned (bool only — never the key itself). Lets
    # the UI render "confirm host key" vs "provision" without a second round-trip.
    host_key_pinned: bool = False
    # Operator diagnostics + maintenance (review: operator console).
    maintenance: bool = False
    last_error: Optional[str] = None
    last_checked_at: Optional[float] = None   # epoch secs of last reachability probe ("last seen")


class NodeRegisterRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    host: str = Field(..., max_length=45)
    role: str = Field("remote_producer", max_length=32)


class NodeAddByHostRequest(BaseModel):
    host: str = Field(..., max_length=45, description="Camera-node IPv4; gateway mints the node_id")
    display_name: Optional[str] = Field(None, max_length=64)


class ProvisionRequest(BaseModel):
    sudo_password: Optional[str] = Field(
        None, description="node sudo password — write-only, held in memory for the run, never stored/logged")
    gateway_host: str = Field(GATEWAY_LAN_IP, max_length=45)
    allow_tofu: bool = Field(
        False, description="dev/bench only: pin the host key on first contact (TOFU) instead of "
                           "requiring an out-of-band confirmed key. Default false (production-safe).")


class HostKeyConfirmRequest(BaseModel):
    expected_fingerprint: str = Field(
        ..., min_length=1, max_length=128,
        description="SHA256:… fingerprint read out-of-band from the node "
                    "(`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`)")
    force: bool = Field(
        False, description="re-pin even when a DIFFERENT key is already pinned (key rotation). "
                           "Default false: an existing pin is never silently replaced.")


class StreamsRequest(BaseModel):
    sensors: list[str] = Field(..., min_length=1, max_length=4,
                               description="subset of color/depth/ir1/ir2 to activate")
    sudo_password: Optional[str] = Field(
        None, description="node sudo password — write-only, never stored/logged")
    gateway_host: str = Field(GATEWAY_LAN_IP, max_length=45)


class NodeCheckRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)


class MaintenanceRequest(BaseModel):
    enabled: bool = Field(..., description="true = pause FDIR for this node while servicing hardware")


class FdirToggleRequest(BaseModel):
    enabled: bool = Field(..., description="false = FDIR stops monitoring/recovering this one binding")


class BindingOut(BaseModel):
    binding_id: str
    node_id: str
    sensor: str
    mode: str
    mountpoint_id: int
    rtp_port: int
    rtp_iface: str
    codec: str
    payload_type: int
    status: str
    fdir_enabled: bool = True
    # Media freshness from janus_summary — only populated when ?include_rtp_age=true
    # (the default list stays cheap: no per-binding Janus admin call per poll).
    rtp_age_ms: Optional[int] = None


class BindingCreateRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    sensor: str = Field(..., pattern=_SENSOR_RE)
    rtp_iface: str = Field(..., max_length=45,
                           description="Gateway LAN IP the producer targets (explicit, never 0.0.0.0)")
    codec: str = Field("h264", pattern=r"^(h264|vp8|vp9|av1)$")
    payload_type: int = Field(96, ge=96, le=127)
    mountpoint_id: Optional[int] = Field(None, ge=1000, le=65535)
    rtp_port: Optional[int] = Field(None, ge=1024, le=65535)


class EnsureJanusResponse(BaseModel):
    status: str
    mountpoint_id: int
    iface: str
    detail: str = ""


class TuningRequest(BaseModel):
    width: Optional[int] = Field(None, ge=160, le=4096)
    height: Optional[int] = Field(None, ge=120, le=2160)
    fps: Optional[int] = Field(None, ge=1, le=120)
    rotation: Optional[int] = Field(None, description="0/90/180/270")
    bitrate_kbps: Optional[int] = Field(None, ge=100, le=20000)
