"""AE-1 — the NEW_SESSIONS_ONLY apply orchestration.

Composes the AE-0 primitives under the apply lock:
  is_applyable → confirm → re-validate → coherence → base-match → set_status(applying) →
  write_locked (file-first, fsync, re-hash TOCTOU) → os.environ + cache_clear → verify
  (models the depth-camera override) → applied | rollback (env+cache first, file last).

ONLY ``webrtc.ice_policy`` + ``webrtc.turn_credential_ttl_seconds``. No encoder/Janus
restart, no mountpoint, no FDIR/quiesce, no reboot. The route maps Outcome → HTTP.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.services import runtime_env_writer as W
from app.services import runtime_revision_store as store
from app.services.store_safety import StoreCorrupt, quarantine_corrupt

log = logging.getLogger(__name__)

# field → env-key (validator _WEBRTC_POLICY is the source of truth: "env ICE_POLICY"/"env TURN_CRED_TTL").
_FIELD_ENV = {"ice_policy": "ICE_POLICY", "turn_credential_ttl_seconds": "TURN_CRED_TTL"}
_ALLOWED_FIELDS = set(_FIELD_ENV)
_UNSET = object()


class Outcome:
    APPLIED = "applied"
    NOT_FOUND = "not_found"
    CONFIRM_MISMATCH = "confirm_mismatch"
    REJECTED = "rejected"
    DRIFT = "drift"
    CONFLICT = "conflict"
    LOCK_HELD = "lock_held"
    WRITE_FAILED = "write_failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


@dataclass
class ApplyResult:
    outcome: str
    revision_id: str
    changed: bool = False
    verified: bool = False
    detail: str = ""
    applied: List[dict] = field(default_factory=list)


def _read_raw_revision(revision_id: str) -> Optional[dict]:
    # The stored record is already redacted at persist; for NEW_SESSIONS_ONLY there are no
    # secret keys, so this is the full record needed for apply (path-traversal guarded).
    if "/" in revision_id or "\\" in revision_id or ".." in revision_id or not revision_id.startswith("rev-"):
        return None
    p = store.REVISION_DIR / f"{revision_id}.json"
    if not p.is_file():
        return None
    try:
        raw = p.read_text()
    except OSError as e:                              # access/IO error — degrade (can't read = can't apply)
        log.warning("revision %s unreadable (%s) — treating as not found", revision_id, e)
        return None
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # A corrupt revision must NOT be hidden as "not found" (it would silently apply nothing / mislead).
        q = quarantine_corrupt(p, f"invalid JSON: {e}")
        raise StoreCorrupt(
            f"revision {revision_id} is not valid JSON ({e}); quarantined {q} — refusing to apply a "
            "corrupt revision") from e
    if not isinstance(rec, dict):
        q = quarantine_corrupt(p, "top-level is not a JSON object")
        raise StoreCorrupt(f"revision {revision_id} top-level is not a JSON object; quarantined {q}")
    return rec


def _expected_effective(target: Dict[str, object]) -> Dict[str, object]:
    """What build_effective() WILL produce for the target — models its conditionals so the
    verify oracle doesn't false-rollback (AE-C10: depth_camera forces relay; ttl is clamped)."""
    from app.core.settings import get_settings
    s = get_settings()
    exp: Dict[str, object] = {}
    if "ice_policy" in target:
        ice = target["ice_policy"] if target["ice_policy"] in ("all", "relay") else "all"
        if s.camera_type == "depth_camera":
            ice = "relay"
        exp["ice_policy"] = ice
    if "turn_credential_ttl_seconds" in target:
        exp["turn_credential_ttl_seconds"] = max(300, min(3600, int(target["turn_credential_ttl_seconds"])))
    return exp


def _verify(target: Dict[str, object]) -> bool:
    from app.services.runtime_config_builder import build_effective
    eff = build_effective().webrtc
    return all(getattr(eff, k) == v for k, v in _expected_effective(target).items())


def _coherence_ok() -> (bool, str):
    """AE-C18: disk rs-runtime.env must agree with the live os.environ for the apply keys —
    refuse to apply on top of a prior rollback_failed divergence."""
    cur = W.read_keys()
    for envk in _FIELD_ENV.values():
        disk, proc = cur.get(envk), os.environ.get(envk)
        if disk is not None and proc is not None and disk != proc:
            return False, f"{envk}: disk={disk!r} != process={proc!r}"
    return True, ""


def apply_revision(revision_id: str, confirm: str) -> ApplyResult:
    if _read_raw_revision(revision_id) is None:
        return ApplyResult(Outcome.NOT_FOUND, revision_id, detail="revision not found")
    try:
        with W.runtime_env_lock(blocking=False):   # AE-C2 separate lock; 423 if held
            return _apply_under_lock(revision_id, confirm)
    except W.LockHeld:
        return ApplyResult(Outcome.LOCK_HELD, revision_id, detail="another apply is in progress")


def _apply_under_lock(revision_id: str, confirm: str) -> ApplyResult:
    rec = _read_raw_revision(revision_id)            # re-read UNDER the lock (AE-C9)
    if rec is None:
        return ApplyResult(Outcome.NOT_FOUND, revision_id)
    ok, why = store.is_applyable(rec)                # status/schema/impact/binding (AE-C1/C5)
    if not ok:
        return ApplyResult(Outcome.REJECTED, revision_id, detail=why)

    diff_hash = rec["diff_hash"]
    if confirm != f"apply-{diff_hash}":
        return ApplyResult(Outcome.CONFIRM_MISMATCH, revision_id, detail="confirm does not match diff_hash")
    if not revision_id.endswith(diff_hash[7:15]):    # AE-C16 id↔hash consistency
        return ApplyResult(Outcome.REJECTED, revision_id, detail="revision_id/diff_hash inconsistent")

    patch = rec.get("validated_patch") or {}
    webrtc = patch.get("webrtc") or {}
    if (set(patch) - {"webrtc", "version"}) or (set(webrtc) - _ALLOWED_FIELDS) or not webrtc:
        return ApplyResult(Outcome.REJECTED, revision_id, detail="patch has forbidden/empty fields for this engine")

    from app.services.runtime_config_validator import validate as _validate
    result = _validate(patch)                        # server-side re-validate (AE-C16)
    if not result.valid:
        return ApplyResult(Outcome.REJECTED, revision_id, detail="re-validate failed")
    if {i.value for i in result.impact} != {"NEW_SESSIONS_ONLY"}:
        return ApplyResult(Outcome.REJECTED, revision_id, detail="impact is not exactly NEW_SESSIONS_ONLY")

    coh, cdetail = _coherence_ok()                   # AE-C18
    if not coh:
        return ApplyResult(Outcome.CONFLICT, revision_id, detail="state divergence: " + cdetail)

    if store.file_hashes_before(patch) != rec["file_hashes_before"]:   # base-match (AE-C1)
        return ApplyResult(Outcome.DRIFT, revision_id, detail="rs-runtime.env changed since validate")

    target = dict(webrtc)
    updates = {_FIELD_ENV[f]: str(int(v) if f == "turn_credential_ttl_seconds" else v)
               for f, v in webrtc.items()}

    if _verify(target):                              # idempotent no-op (AE-C20)
        store.set_status(revision_id, store.STATUS_APPLIED)
        return ApplyResult(Outcome.APPLIED, revision_id, changed=False, verified=True, detail="already at target")

    rt = store.RUNTIME_ENV_PATH
    prior_bytes = rt.read_bytes() if rt.is_file() else None
    prior_mode = (os.stat(rt).st_mode & 0o777) if rt.is_file() else 0o644
    prior_os = {k: os.environ.get(k, _UNSET) for k in updates}   # AE-C3 unset sentinel
    prior_hash = W.current_hash()

    store.set_status(revision_id, store.STATUS_APPLYING)
    try:                                             # file-first; re-hash under lock (AE-C19)
        W.write_locked(updates, expected_hash=prior_hash)
    except W.DriftError:
        store.set_status(revision_id, store.STATUS_VALIDATED)
        return ApplyResult(Outcome.DRIFT, revision_id, detail="rs-runtime.env changed during apply")
    except Exception as e:                           # write failed → NO live change yet (AE risk 6)
        store.set_status(revision_id, store.STATUS_VALIDATED)
        return ApplyResult(Outcome.WRITE_FAILED, revision_id, detail=f"write failed: {e}")

    for k, v in updates.items():                     # activate
        os.environ[k] = v
    _cache_clear()

    if _verify(target):
        store.set_status(revision_id, store.STATUS_APPLIED)
        applied = [{"path": f"webrtc.{f}", "to": v} for f, v in webrtc.items()]
        return ApplyResult(Outcome.APPLIED, revision_id, changed=True, verified=True, applied=applied)

    return _rollback(revision_id, prior_os, prior_bytes, prior_mode)


def _rollback(revision_id, prior_os, prior_bytes, prior_mode) -> ApplyResult:
    store.set_status(revision_id, store.STATUS_ROLLING_BACK)
    for k, prior in prior_os.items():                # env + cache first (in-proc) — AE-C13
        if prior is _UNSET:
            os.environ.pop(k, None)                   # AE-C3 rollback-to-unset
        else:
            os.environ[k] = prior
    _cache_clear()
    try:                                             # file restore LAST, under the held lock
        W.restore_locked(prior_bytes, mode=prior_mode)
        store.set_status(revision_id, store.STATUS_ROLLED_BACK)
        return ApplyResult(Outcome.ROLLED_BACK, revision_id, detail="verify failed; rolled back")
    except Exception as e:                           # AE-C4: a rejected value may be durable on disk
        store.set_status(revision_id, store.STATUS_ROLLBACK_FAILED)
        log.critical("apply %s rollback file-restore FAILED: %s — disk may hold the rejected value; "
                     "recover_on_boot will refuse it", revision_id, e)
        return ApplyResult(Outcome.ROLLBACK_FAILED, revision_id, detail=f"rollback failed: {e}")


def _cache_clear() -> None:
    from app.core.settings import get_settings
    get_settings.cache_clear()


def recover_on_boot() -> int:
    """Reconcile revisions stuck in applying/rolling_back against the on-disk hash (AE-C12).
    Conservative + safe: an applying revision whose disk == base (write never landed) →
    rolled_back; else → applied (the value is live via systemd reload). A rolling_back whose
    disk != base → rollback_failed (a rejected value may be durable — surface it, AE-C4).
    Returns the number reconciled. No-op when nothing is stuck."""
    n = 0
    for rid in store.list_revisions():
        rec = _read_raw_revision(rid)
        if not rec:
            continue
        st = rec.get("status")
        if st not in (store.STATUS_APPLYING, store.STATUS_ROLLING_BACK):
            continue
        base = (rec.get("file_hashes_before") or {}).get(str(store.RUNTIME_ENV_PATH))
        disk = W.current_hash()
        if st == store.STATUS_APPLYING:
            store.set_status(rid, store.STATUS_ROLLED_BACK if disk == base else store.STATUS_APPLIED)
        else:  # rolling_back
            store.set_status(rid, store.STATUS_ROLLED_BACK if disk == base else store.STATUS_ROLLBACK_FAILED)
        log.warning("recover_on_boot: revision %s was %s → reconciled (disk==base: %s)", rid, st, disk == base)
        n += 1
    return n
