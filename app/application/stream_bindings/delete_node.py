"""Use-case: forget a remote host (the gateway-side cascade), SYNCHRONOUS. Verbatim from
routes/stream_bindings.delete_node (Phase 12.2). Tears down each binding's Janus mountpoint,
removes the node row + all its bindings (the one must-succeed store mutation), then reconciles
the per-node RTP firewall. deprovision=true ALSO best-effort stops live encoders first. Every
step except the store removal is best-effort/swallowed; partial outcomes are surfaced in the
result (destroyed_mountpoints / firewall_reconciled). node_client / janus_admin / firewall_sync
are called, never changed."""
from __future__ import annotations

import logging

from app.services import janus_admin, node_client
from app.services import stream_binding_store as sbs
from app.services.audit_log import audit

from app.application.stream_bindings.commands import DeleteNodeCommand
from app.application.stream_bindings.results import (
    DeleteNodeResult,
    LocalNodeNotRemovable,
    NodeNotFound,
)

log = logging.getLogger("stream_bindings.delete_node")


def delete_node(cmd: DeleteNodeCommand) -> DeleteNodeResult:
    node = sbs.get_node(cmd.node_id, state_path=cmd.bind_state_path)
    if node is None:
        raise NodeNotFound(cmd.node_id)
    if node.node_id == sbs.LOCAL_NODE_ID:
        raise LocalNodeNotRemovable()

    bindings = sbs.list_bindings(state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
    node_bindings = [b for b in bindings.values()
                     if b.node_id == cmd.node_id and b.mode == sbs.StreamMode.REMOTE_PRODUCER]

    if cmd.deprovision:                   # best-effort: stop live encoders on the node first
        client = node_client.get_node_client(cmd.node_id, state_path=cmd.bind_state_path)
        for b in node_bindings:
            try:
                client.stop_stream(cmd.node_id, b.sensor)
            except Exception as e:        # never fatal — we are removing the host anyway
                log.warning("deprovision stop_stream %s:%s: %s", cmd.node_id, b.sensor, e)

    from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
    destroyed = []
    for b in node_bindings:               # best-effort Janus teardown per mountpoint
        try:
            janus_admin.destroy_mountpoint(mp_id=b.janus.mountpoint_id, mp_secret=MP_DEFAULT_SECRET)
            destroyed.append(b.janus.mountpoint_id)
        except Exception as e:
            log.warning("destroy_mountpoint(%d) for %s: %s — proceeding",
                        b.janus.mountpoint_id, b.binding_id, e)

    outcome = sbs.remove_node(cmd.node_id, state_path=cmd.bind_state_path)

    try:                                  # drop the now-stale per-node firewall ACCEPTs
        from app.services import firewall_sync
        firewall_sync.reconcile(state_path=cmd.bind_state_path,
                                alloc_state_path=cmd.alloc_state_path, apply=True)
        firewall_reconciled = True
    except Exception as e:
        log.warning("post-remove firewall reconcile failed for %s: %s", cmd.node_id, e)
        firewall_reconciled = False

    audit("stream_bindings.node.delete",
          {"node_id": cmd.node_id, "host": node.host, "deprovision": cmd.deprovision,
           "removed_bindings": outcome["binding_ids"], "destroyed_mountpoints": destroyed})
    return DeleteNodeResult(
        node_id=cmd.node_id, removed=outcome["removed"],
        removed_bindings=outcome["binding_ids"], destroyed_mountpoints=destroyed,
        firewall_reconciled=firewall_reconciled, deprovisioned=cmd.deprovision)
