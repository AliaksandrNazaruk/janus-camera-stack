"""Run a long node op (provision / rotate-token / activate) in a daemon thread, recording its
lifecycle in the durable operation_journal. Replaces the in-memory `_inflight` guard: one
running op per node (durable 409 via OperationConflict), and restart-orphaned ops are reaped on
startup. The daemon thread is KEPT (immune to the response lifecycle — the original Bug-A reason
that ruled out Starlette BackgroundTasks); only durability is added around it.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import List

from app.services import node_provisioner
from app.services import operation_journal as journal
from app.services import stream_binding_store as sbs

log = logging.getLogger("node_operation_runner")


def run(node_id: str, op_type: str, fn, *args, ops_path=None, **kwargs) -> str:
    """Begin a durable op (raises operation_journal.OperationConflict → the route maps it to 409),
    then run `fn(*args, **kwargs)` in a daemon thread, recording succeeded / failed (+ last_error).
    `ops_path` is the journal file (the route derives it from the store dir so tests' path-redirect
    covers it; None → operation_journal.DEFAULT_OPS_PATH). Returns the operation_id (uuid4)."""
    op_id = uuid.uuid4().hex
    journal.begin(node_id, op_type, op_id, path=ops_path)   # OperationConflict if already running

    def _run() -> None:
        try:
            fn(*args, **kwargs)
            status, last_error = "succeeded", ""
        except Exception as e:  # noqa: BLE001 — the op records its own store status; we record op-level
            log.exception("node op %s for %s failed", op_type, node_id)
            status, last_error = "failed", str(e)
        # Record the op-level outcome. If the journal went corrupt mid-op, finish raises
        # JournalCorrupt (the file is quarantined by _load); don't crash the daemon thread —
        # the node/store status is authoritative (H3).
        try:
            journal.finish(op_id, status, last_error=last_error, path=ops_path)
        except journal.JournalCorrupt:
            log.critical("op %s: journal corrupt at finish; %r outcome not recorded "
                         "(node/store status is authoritative)", op_id, status)

    threading.Thread(target=_run, name=f"{op_type}:{node_id}", daemon=True).start()
    return op_id


def reap_orphans(*, state_path=sbs.DEFAULT_STATE_PATH, ops_path=None) -> List[dict]:
    """Startup recovery: any op still `running` lost its daemon thread on restart → mark it
    `interrupted`. R1 un-stick: if the node is still mid-provision (provision_state in the
    in-progress set), flip provision_state → failed (retriable) with a clear last_error; terminal
    states (ready / failed / no_camera) are never clobbered. Returns the reaped ops."""
    reaped: List[dict] = []
    try:
        running = journal.all_running(path=ops_path)
    except journal.JournalCorrupt:
        # _load already quarantined the bad file + logged CRITICAL. Boot must NOT block on a corrupt
        # history file (H3 startup policy): continue with a fresh empty journal — nothing to reap.
        log.critical("reap: operations journal was corrupt; quarantined, continuing with empty journal")
        return reaped
    for op in running:
        journal.finish(op["operation_id"], "interrupted",
                       last_error="interrupted by gateway restart", path=ops_path)
        try:
            node = sbs.get_node(op["node_id"], state_path=state_path)
            if node is not None and node_provisioner.is_in_progress(node.provision_state):
                sbs.set_provision_state(
                    op["node_id"], node_provisioner.PState.FAILED, state_path=state_path,
                    detail=f"interrupted by restart: {op['op_type']}")
        except Exception:  # noqa: BLE001
            log.exception("reap: un-stick failed for node %s", op["node_id"])
        reaped.append(op)
    return reaped
