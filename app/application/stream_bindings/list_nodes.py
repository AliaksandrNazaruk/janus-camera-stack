"""Use-case: list all nodes (incl. the implicit local gateway camera). Verbatim read from
routes/stream_bindings.get_nodes (Phase 12.3A). Returns the domain NodeEntry map; the route
maps each entry to the NodeOut DTO. sbs is called, never changed."""
from __future__ import annotations

from app.services import stream_binding_store as sbs

from app.application.stream_bindings.commands import ListNodesCommand


def list_nodes(cmd: ListNodesCommand):
    return sbs.list_nodes(state_path=cmd.bind_state_path)
