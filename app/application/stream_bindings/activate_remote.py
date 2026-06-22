"""Use-case (async op body): activate a REMOTE node's streams over SSH, then reconcile its RTP
firewall. Verbatim from routes/stream_bindings._activate_then_firewall (Phase 12.1) — fired via
node_operation_runner.run, so success/failure land in the operation journal. firewall_sync.reconcile
stays best-effort (swallowed). node_provisioner.activate_streams / firewall_sync are untouched."""
from __future__ import annotations

import logging

from app.services import node_provisioner

log = logging.getLogger("stream_bindings.activate")


def activate_remote(node_id, *, transport, sensors, gateway_host, binder,
                    bind_state_path, alloc_state_path) -> None:
    node_provisioner.activate_streams(
        node_id, transport, sensors=sensors, gateway_host=gateway_host,
        on_bind=binder, state_path=bind_state_path)
    # ensure_janus only PREPARES Janus; the per-node RTP firewall is separate. Reconcile it so
    # onboarding is self-contained (open the bindings' ports). Best-effort — never fatal to the op.
    try:
        from app.services import firewall_sync
        firewall_sync.reconcile(state_path=bind_state_path,
                                alloc_state_path=alloc_state_path, apply=True)
    except Exception:
        log.exception("post-activate firewall reconcile failed for %s", node_id)
