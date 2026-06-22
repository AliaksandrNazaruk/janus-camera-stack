"""Cycle 13 — a bounded, observable local-Janus restart operation (additive to sync /janus/restart).

Reuses the durable operation primitives (`operation_journal` via `node_operation_runner`) with a
SYNTHETIC scope `local_janus` — no real node, the journal's `node_id` is just a conflict key. Free
wins: one restart at a time (`OperationConflict` -> route 409); `running -> succeeded/failed`
recording; startup `reap_orphans()` -> `interrupted`; surfacing through the existing `/operations`
+ `/operations/{id}` endpoints + the canonical `OperationStatus`.

NOT a generic AdminOperationRunner (Cycle 8 rejected that); does NOT touch the NAT operation; and
does NOT change the synchronous `/janus/restart` (a depth-peer machine client relies on its 200=done
semantics; see docs/design/JANUS_RESTART_OPERATION.md)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.services import nat_config, node_operation_runner

#: synthetic operation_journal scope/type for a local Janus restart (not a real node).
JANUS_RESTART_SCOPE = "local_janus"
JANUS_RESTART_OP_TYPE = "janus_restart"


def start_tracked_restart(*, ops_path: Optional[Path] = None) -> str:
    """Start a tracked local-Janus restart in a background daemon thread; return the operation_id
    (poll `/api/v1/admin/operations/{id}`). Raises `operation_journal.OperationConflict` if one is
    already running (route -> 409). `ops_path=None` -> the default journal beside the binding store
    (the same file `/operations` reads); tests pass an explicit path."""
    return node_operation_runner.run(
        JANUS_RESTART_SCOPE, JANUS_RESTART_OP_TYPE, nat_config.restart_janus, ops_path=ops_path)
