"""Result + domain-error types for stream-binding use-cases. No FastAPI — the route maps
these to HTTP (BindingNotFound→404, UnsupportedSensorError→400, ok is False→502)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BindingOpResult:
    binding_id: str
    ok: bool
    detail: str


@dataclass
class RemoveBindingResult:
    binding_id: str
    removed: bool


@dataclass
class EnsureJanusResult:
    status: str
    mountpoint_id: int
    iface: str
    detail: str = ""


@dataclass
class DeleteNodeResult:
    node_id: str
    removed: bool
    removed_bindings: list
    destroyed_mountpoints: list
    firewall_reconciled: bool
    deprovisioned: bool


class NodeNotFound(Exception):
    """No node with this id. Route maps to 404 `unknown node <id>`."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"unknown node {node_id}")


class LocalNodeNotRemovable(Exception):
    """The local node 'cam10' is implicit and cannot be removed. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("the local node 'cam10' is implicit and cannot be removed")


class BindingNotFound(Exception):
    """No binding with this id. Route maps to 404 `unknown binding <id>`."""

    def __init__(self, binding_id: str) -> None:
        self.binding_id = binding_id
        super().__init__(f"unknown binding {binding_id}")


class UnsupportedSensorError(Exception):
    """The binding's sensor isn't supported by the local lifecycle. Route maps to 400."""


class LocalFdirNotToggleable(Exception):
    """A local projection binding has no per-binding FDIR toggle (local FDIR is the cam10
    watchdog ladder). Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("local FDIR is the cam10 watchdog ladder, not a per-binding toggle")


class LocalBindingNotRemovable(Exception):
    """A local projection binding can't be removed here (manage via the camera lifecycle).
    Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("cannot remove a local projection; manage local streams "
                         "via the camera lifecycle")


class EnsureJanusLocalRejected(Exception):
    """ensure-janus is for remote bindings; local streams are provisioned by the camera
    lifecycle. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("ensure-janus is for remote bindings; local streams are "
                         "provisioned by the camera lifecycle")


class LocalTuningRejected(Exception):
    """Remote tuning endpoint hit for a local binding. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("local stream tuning is at /cameras/{serial}/{sensor}/config")


class InvalidRotation(Exception):
    """rotation must be 0/90/180/270. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("rotation must be 0/90/180/270")


class NoTuningFields(Exception):
    """A tuning write needs at least one field. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("no tuning fields provided")


class NodeAgentError(Exception):
    """A node-agent tuning read/write failed. Route maps to 502 (message carried verbatim)."""


class ManifestInvalid(Exception):
    """The declarative fleet manifest failed to load/validate. Route maps to 422
    (message carried verbatim)."""


class JanusUnreachable(Exception):
    """Live Janus mountpoint listing failed during a read-only drift check. Route maps to
    503 `janus_unreachable: <reason>` (reason already truncated to 120 chars)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class MaintenanceLocalRejected(Exception):
    """Node maintenance was requested for the local node. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("the local node uses the cam10 watchdog ladder, not node maintenance")


class LocalNodeNoHostKey(Exception):
    """A host-key op was requested for the local node, which has no remote SSH host key.
    Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("the local node has no remote SSH host key")


class HostKeyUnreachable(Exception):
    """Could not reach the node to capture its SSH host key. Route maps to 503."""

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(f"could not reach {host} to capture its host key")


class HostKeyFingerprintMismatch(Exception):
    """The captured fingerprint did not match the operator-supplied one — nothing pinned.
    Route maps to 409."""

    def __init__(self, seen: str, expected: str) -> None:
        self.seen = seen
        self.expected = expected
        super().__init__(
            f"fingerprint mismatch — gateway sees {seen!r}, you expected {expected!r}; NOT pinned")


class HostKeyPinReplaceRejected(Exception):
    """A different host key is already pinned; replacing a confirmed pin needs force=true.
    Route maps to 409."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(
            f"a different host key is already pinned for {node_id}; replacing a confirmed "
            f"pin is key rotation — pass force=true to re-pin")


class LocalBindingNotCreatable(Exception):
    """Local bindings are projections, not created via the remote-binding API. Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("local bindings are projections, not created via this API")


class BindingNodeNotFound(Exception):
    """No node with this id when creating a binding — register it first. Route maps to 404
    (distinct message from the generic NodeNotFound)."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"unknown node {node_id} — register it first")


class AllocationConflict(Exception):
    """Mountpoint/port allocation failed (exhausted/conflict). Route maps to 409
    (message carried verbatim from the allocator)."""


class BindingInvalid(Exception):
    """The binding failed store validation (e.g. a non-LAN rtp_iface). Route maps to 400
    (message carried verbatim)."""


class NodeRegistrationInvalid(Exception):
    """upsert_node / add_node_by_host failed store validation. Route maps to 400
    (message carried verbatim)."""


class AddNodeIsLocalGateway(Exception):
    """The supplied host is the local gateway itself (its camera is the built-in cam10 host),
    not a remote node. Route maps to 400."""

    def __init__(self, host: str, local_node_id: str) -> None:
        self.host = host
        super().__init__(f"{host} is the local gateway — its camera is the built-in "
                         f"'{local_node_id}' host (already listed); no remote node needed")


# ── Phase 3 — node provision / rotate-token (durable async ops) ───────


@dataclass
class NodeOpStarted:
    """A durable node op (provision / rotate-token) was accepted and spawned. The route shapes
    the H1 response ({operation_id, poll, operation}) from this."""
    host: str
    operation_id: str


class ProvisionLocalRejected(Exception):
    """Provision/activate was requested for the local node (the built-in cam10 host).
    Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("cannot provision/activate the local node")


class RotateTokenLocalRejected(Exception):
    """Token rotation was requested for the local node, which has no remote agent token.
    Route maps to 400."""

    def __init__(self) -> None:
        super().__init__("the local node has no remote agent token")


class NodeBundleMissing(Exception):
    """The node deploy bundle has not been built. Route maps to 503 (message carries the path)."""

    def __init__(self, bundle_tar: str) -> None:
        self.bundle_tar = bundle_tar
        super().__init__(f"node bundle not built at {bundle_tar}")
