"""Use-case: list stream bindings (local projections + remote), optionally enriched with
per-mountpoint RTP media-freshness. Verbatim from routes/stream_bindings.get_stream_bindings
(Phase 12.3A). The Janus freshness probe is INJECTED (rtp_age_fn) — best-effort, route-owned —
so this use-case stays free of the Janus client. Returns (binding, rtp_age_ms) pairs; the route
maps each to the BindingOut DTO. sbs is called, never changed."""
from __future__ import annotations

from typing import Callable, Optional

from app.services import stream_binding_store as sbs

from app.application.stream_bindings.commands import ListBindingsCommand


def list_bindings(cmd: ListBindingsCommand, *, rtp_age_fn: Callable[[int], Optional[int]]):
    bindings = sbs.list_bindings(state_path=cmd.bind_state_path, alloc_state_path=cmd.alloc_state_path)
    out = []
    for b in bindings.values():
        age = rtp_age_fn(b.janus.mountpoint_id) if cmd.include_rtp_age else None
        out.append((b, age))
    return out
