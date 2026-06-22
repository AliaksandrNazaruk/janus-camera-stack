"""Read-only desired/actual drift for the gateway control plane.

Materializes `docs/design/DESIRED_ACTUAL_RECONCILE_MODEL.md` as an observable report
WITHOUT acting on it. `compute_drift` is PURE — the binding set, the live Janus
mountpoint ids, and an RTP-age probe are all INJECTED, so it cannot touch Janus,
systemd, the firewall, or secrets (invariant **R4**: read-only by construction). The
HTTP route supplies the real reads; on a corrupt desired store `list_bindings` raises
and the app returns 503 `topology_store_corrupt` (**R5**) — this function is never
reached with a fabricated empty desired set.

Classifications (per remote binding):
  in_sync                     active, mountpoint present, RTP fresh
  missing_janus_mountpoint    active, mountpoint absent                    -> DRIFT
  stale_rtp                   active, mountpoint present, RTP missing/old  -> DRIFT
  stopped_by_operator         fdir.enabled=false OR configured_offline     -> NOT drift (R2)
And, across the fleet:
  unexpected_janus_mountpoint a live remote-range mp with no binding       -> DRIFT
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

from app.services import stream_binding_store as sbs

IN_SYNC = "in_sync"
MISSING_JANUS_MOUNTPOINT = "missing_janus_mountpoint"
UNEXPECTED_JANUS_MOUNTPOINT = "unexpected_janus_mountpoint"
STOPPED_BY_OPERATOR = "stopped_by_operator"
STALE_RTP = "stale_rtp"

# per-binding classifications that count as drift (unexpected mountpoints are fleet-level)
_DRIFT_CLASSES = {MISSING_JANUS_MOUNTPOINT, STALE_RTP}


def _is_operator_stopped(b: "sbs.StreamBinding", maintenance_node_ids=frozenset()) -> bool:
    """R2 — operator Stop: FDIR disabled, OR created-but-never-activated
    (configured_offline), OR the node is under maintenance. Such a binding's mountpoint
    absence is EXPECTED, not drift — and the run-once reconcile must not resurrect it."""
    return ((not b.fdir.enabled)
            or b.status == sbs.StreamStatus.CONFIGURED_OFFLINE.value
            or b.node_id in maintenance_node_ids)


def compute_drift(bindings: Dict[str, "sbs.StreamBinding"],
                  live_mp_ids: Iterable[int],
                  *,
                  rtp_age_fn: Optional[Callable[[int], Optional[int]]] = None,
                  stale_ms: int = 4000,
                  maintenance_node_ids=frozenset()) -> dict:
    """Classify each REMOTE binding's desired vs actual. Pure; deterministic (R9)."""
    live = {int(m) for m in live_mp_ids}
    items = []
    known_remote_mps: set = set()           # every remote binding's mp (active OR stopped)

    for bid, b in bindings.items():
        if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
            continue                        # desired set = remote producer bindings only
        mp = int(b.janus.mountpoint_id)
        known_remote_mps.add(mp)
        rec = {"binding_id": bid, "node_id": b.node_id, "sensor": b.sensor,
               "mountpoint_id": mp, "status": b.status,
               "fdir_enabled": bool(b.fdir.enabled),
               "mountpoint_present": mp in live}
        if _is_operator_stopped(b, maintenance_node_ids):
            rec["classification"] = STOPPED_BY_OPERATOR     # R2: terminal, never drift
            items.append(rec)
            continue
        if mp not in live:
            rec["classification"] = MISSING_JANUS_MOUNTPOINT
            items.append(rec)
            continue
        age = rtp_age_fn(mp) if rtp_age_fn else None
        rec["video_age_ms"] = age
        rec["classification"] = STALE_RTP if (age is None or age > stale_ms) else IN_SYNC
        items.append(rec)

    # A live REMOTE-range mountpoint with NO known binding is an orphan listener.
    # Local static ids (< REMOTE_MP_MIN, e.g. cam10's 1305-1308) are out of scope.
    unexpected = sorted(m for m in live
                        if m >= sbs.REMOTE_MP_MIN and m not in known_remote_mps)

    counts: Dict[str, int] = {}
    for it in items:
        counts[it["classification"]] = counts.get(it["classification"], 0) + 1
    if unexpected:
        counts[UNEXPECTED_JANUS_MOUNTPOINT] = len(unexpected)

    drift = bool(unexpected) or any(it["classification"] in _DRIFT_CLASSES for it in items)
    return {"topology_store_corrupt": False, "drift": drift, "counts": counts,
            "bindings": items, "unexpected_mountpoints": unexpected}
