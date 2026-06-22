"""Stream-binding use-cases — orchestration extracted from routes/stream_bindings.py
(Phase 10 / D1). Plain functions over commands → results; no FastAPI in this layer.
The route maps results/domain-errors to HTTP. Grown one small vertical at a time
(see docs/design/STREAM_BINDINGS_EXTRACTION.md)."""
from app.application.stream_bindings.commands import (
    ActivateLocalCommand,
    AddNodeCommand,
    CheckNodeCommand,
    ConfirmHostKeyCommand,
    CreateBindingCommand,
    DeleteNodeCommand,
    EnsureJanusCommand,
    FirewallReconcileCommand,
    FleetPlanCommand,
    FleetReconcileCommand,
    GetHostKeyCommand,
    GetTuningCommand,
    ListBindingsCommand,
    ListNodesCommand,
    ProvisionNodeCommand,
    ReconcileDriftCommand,
    RegisterNodeCommand,
    RemoveBindingCommand,
    RestartBindingCommand,
    RotateTokenCommand,
    SetFdirCommand,
    SetMaintenanceCommand,
    SetTuningCommand,
    StopBindingCommand,
)
from app.application.stream_bindings.results import (
    AddNodeIsLocalGateway,
    AllocationConflict,
    BindingInvalid,
    BindingNodeNotFound,
    BindingNotFound,
    BindingOpResult,
    DeleteNodeResult,
    EnsureJanusLocalRejected,
    EnsureJanusResult,
    HostKeyFingerprintMismatch,
    HostKeyPinReplaceRejected,
    HostKeyUnreachable,
    InvalidRotation,
    JanusUnreachable,
    LocalBindingNotCreatable,
    LocalBindingNotRemovable,
    LocalFdirNotToggleable,
    LocalNodeNoHostKey,
    LocalNodeNotRemovable,
    LocalTuningRejected,
    MaintenanceLocalRejected,
    ManifestInvalid,
    NoTuningFields,
    NodeAgentError,
    NodeBundleMissing,
    NodeNotFound,
    NodeOpStarted,
    NodeRegistrationInvalid,
    ProvisionLocalRejected,
    RemoveBindingResult,
    RotateTokenLocalRejected,
    UnsupportedSensorError,
)
from app.application.stream_bindings.activate_local import activate_local
from app.application.stream_bindings.activate_remote import activate_remote
from app.application.stream_bindings.add_node import add_node
from app.application.stream_bindings.check_node import check_node
from app.application.stream_bindings.confirm_host_key import confirm_host_key
from app.application.stream_bindings.create_binding import create_binding
from app.application.stream_bindings.delete_node import delete_node
from app.application.stream_bindings.ensure_janus import ensure_janus
from app.application.stream_bindings.firewall_reconcile import firewall_reconcile
from app.application.stream_bindings.fleet_plan import fleet_plan, plan_dict
from app.application.stream_bindings.fleet_reconcile import fleet_reconcile
from app.application.stream_bindings.get_host_key import get_host_key
from app.application.stream_bindings.get_tuning import get_tuning
from app.application.stream_bindings.get_modes import get_modes
from app.application.stream_bindings.list_bindings import list_bindings
from app.application.stream_bindings.list_nodes import list_nodes
from app.application.stream_bindings.provision_node import provision_node
from app.application.stream_bindings.reconcile_drift import reconcile_drift
from app.application.stream_bindings.register_node import register_node
from app.application.stream_bindings.remove_binding import remove_binding
from app.application.stream_bindings.resolve_provision_target import resolve_provision_target
from app.application.stream_bindings.rotate_node_token import rotate_node_token
from app.application.stream_bindings.restart_binding import restart_binding
from app.application.stream_bindings.set_fdir import set_fdir
from app.application.stream_bindings.set_maintenance import set_maintenance
from app.application.stream_bindings.set_tuning import set_tuning
from app.application.stream_bindings.stop_binding import stop_binding

__all__ = [
    "RestartBindingCommand",
    "StopBindingCommand",
    "SetFdirCommand",
    "RemoveBindingCommand",
    "EnsureJanusCommand",
    "GetTuningCommand",
    "SetTuningCommand",
    "ActivateLocalCommand",
    "DeleteNodeCommand",
    "ListNodesCommand",
    "ListBindingsCommand",
    "FleetPlanCommand",
    "ReconcileDriftCommand",
    "CheckNodeCommand",
    "SetMaintenanceCommand",
    "GetHostKeyCommand",
    "ConfirmHostKeyCommand",
    "CreateBindingCommand",
    "FleetReconcileCommand",
    "FirewallReconcileCommand",
    "RegisterNodeCommand",
    "AddNodeCommand",
    "BindingOpResult",
    "DeleteNodeResult",
    "NodeNotFound",
    "LocalNodeNotRemovable",
    "RemoveBindingResult",
    "EnsureJanusResult",
    "BindingNotFound",
    "UnsupportedSensorError",
    "LocalFdirNotToggleable",
    "LocalBindingNotRemovable",
    "EnsureJanusLocalRejected",
    "LocalTuningRejected",
    "InvalidRotation",
    "NoTuningFields",
    "NodeAgentError",
    "ManifestInvalid",
    "JanusUnreachable",
    "MaintenanceLocalRejected",
    "LocalNodeNoHostKey",
    "HostKeyUnreachable",
    "HostKeyFingerprintMismatch",
    "HostKeyPinReplaceRejected",
    "LocalBindingNotCreatable",
    "BindingNodeNotFound",
    "AllocationConflict",
    "BindingInvalid",
    "NodeRegistrationInvalid",
    "AddNodeIsLocalGateway",
    "restart_binding",
    "stop_binding",
    "set_fdir",
    "remove_binding",
    "ensure_janus",
    "get_tuning",
    "get_modes",
    "set_tuning",
    "activate_local",
    "activate_remote",
    "delete_node",
    "list_nodes",
    "list_bindings",
    "fleet_plan",
    "plan_dict",
    "reconcile_drift",
    "check_node",
    "set_maintenance",
    "get_host_key",
    "confirm_host_key",
    "create_binding",
    "fleet_reconcile",
    "firewall_reconcile",
    "register_node",
    "add_node",
    # Phase 3 — node provision / rotate-token
    "ProvisionNodeCommand",
    "RotateTokenCommand",
    "NodeOpStarted",
    "ProvisionLocalRejected",
    "RotateTokenLocalRejected",
    "NodeBundleMissing",
    "provision_node",
    "rotate_node_token",
    "resolve_provision_target",
]
