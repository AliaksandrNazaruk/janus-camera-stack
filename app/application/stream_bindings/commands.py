"""Command inputs for stream-binding use-cases. State paths are INJECTED by the route
(they're route-module constants the tests patch), keeping the use-case path-agnostic."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RestartBindingCommand:
    binding_id: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class StopBindingCommand:
    binding_id: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class SetFdirCommand:
    binding_id: str
    enabled: bool
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class RemoveBindingCommand:
    binding_id: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class EnsureJanusCommand:
    binding_id: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class GetTuningCommand:
    binding_id: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class SetTuningCommand:
    binding_id: str
    tuning: dict  # already model_dump(exclude_none=True) by the route
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class ActivateLocalCommand:
    sensors: list  # list[str]
    alloc_state_path: Path


@dataclass
class DeleteNodeCommand:
    node_id: str
    deprovision: bool
    bind_state_path: Path
    alloc_state_path: Path


# ── 12.3A read/list/view ──────────────────────────────────────────────


@dataclass
class ListNodesCommand:
    bind_state_path: Path


@dataclass
class ListBindingsCommand:
    include_rtp_age: bool
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class FleetPlanCommand:
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class ReconcileDriftCommand:
    bind_state_path: Path
    alloc_state_path: Path


# ── 12.3B node check / maintenance / host-key ─────────────────────────


@dataclass
class CheckNodeCommand:
    node_id: str
    bind_state_path: Path


@dataclass
class SetMaintenanceCommand:
    node_id: str
    enabled: bool
    bind_state_path: Path


@dataclass
class GetHostKeyCommand:
    node_id: str
    bind_state_path: Path


@dataclass
class ConfirmHostKeyCommand:
    node_id: str
    expected_fingerprint: str
    force: bool
    bind_state_path: Path


# ── 12.3C create binding / fleet reconcile / firewall reconcile ───────


@dataclass
class CreateBindingCommand:
    node_id: str
    sensor: str
    mountpoint_id: Optional[int]
    rtp_port: Optional[int]
    payload_type: int
    codec: str
    rtp_iface: str
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class FleetReconcileCommand:
    bind_state_path: Path
    alloc_state_path: Path


@dataclass
class FirewallReconcileCommand:
    apply: bool
    bind_state_path: Path
    alloc_state_path: Path


# ── 12.3D node register / add-by-host ─────────────────────────────────


@dataclass
class RegisterNodeCommand:
    node_id: str
    host: str
    role: str
    bind_state_path: Path


@dataclass
class AddNodeCommand:
    host: str
    display_name: Optional[str]
    gateway_lan_ip: str
    bind_state_path: Path


# ── Phase 3 — node provision / rotate-token (durable async ops) ───────


@dataclass
class ProvisionNodeCommand:
    node_id: str
    sudo_password: Optional[str]
    allow_tofu: bool
    bind_state_path: Path
    bundle_tar: str


@dataclass
class RotateTokenCommand:
    node_id: str
    sudo_password: Optional[str]
    bind_state_path: Path
