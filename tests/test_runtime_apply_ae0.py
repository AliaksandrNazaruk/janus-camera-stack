"""AE-0 — apply-safe primitives (the building blocks under the AE-1 /apply engine). Proves
the two reproduced v1 criticals are closed: AE-C1 (hash binding fail-open) and AE-C2 (writer
self-deadlock), plus the state-machine setter, the structural stale-revision rejection, and the safe writer.
"""
import os

import pytest

from app.services import runtime_revision_store as store
from app.services import runtime_env_writer as W
from app.services.runtime_env_writer import (
    ForeignKeyError, DriftError, write_runtime_env, current_hash, apply_lock_path,
)


# ── AE-C1: file_hashes_before binds rs-runtime.env UNCONDITIONALLY ───────────

@pytest.mark.parametrize("patch", [
    {"webrtc": {"ice_policy": "relay"}},
    {"webrtc": {"turn_credential_ttl_seconds": 1800}},
    {"webrtc": {"ice_policy": "all", "turn_credential_ttl_seconds": 1200}},
])
def test_fhb_binds_runtime_env_present(tmp_path, monkeypatch, patch):
    rt = tmp_path / "rs-runtime.env"; rt.write_text("ICE_POLICY=relay\n")
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)
    fhb = store.file_hashes_before(patch)
    assert str(rt) in fhb and fhb[str(rt)].startswith("sha256:") and fhb[str(rt)] != store.RUNTIME_ENV_SENTINEL

def test_fhb_absent_is_sentinel_not_empty(tmp_path, monkeypatch):
    rt = tmp_path / "rs-runtime.env"  # absent
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)
    fhb = store.file_hashes_before({"webrtc": {"ice_policy": "relay"}})
    assert fhb == {str(rt): store.RUNTIME_ENV_SENTINEL}    # NOT {} → cannot fail open

def test_fhb_absent_vs_present_differ(tmp_path, monkeypatch):
    # validate-when-absent must NOT base-match an apply-when-present (the AE-C1 hole).
    rt = tmp_path / "rs-runtime.env"
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)
    fhb_absent = store.file_hashes_before({"webrtc": {"ice_policy": "relay"}})
    rt.write_text("ICE_POLICY=relay\n")
    fhb_present = store.file_hashes_before({"webrtc": {"ice_policy": "relay"}})
    assert fhb_absent != fhb_present
    assert store.compute_diff_hash({"webrtc": {"ice_policy": "relay"}}, fhb_absent) \
        != store.compute_diff_hash({"webrtc": {"ice_policy": "relay"}}, fhb_present)


# ── AE-C1/AE-C5: is_applyable structural rejection ───────────────────────────

def _rec(**over):
    base = {
        "status": store.STATUS_VALIDATED,
        "fhb_schema": store.FHB_SCHEMA,
        "impact": ["NEW_SESSIONS_ONLY"],
        "file_hashes_before": {str(store.RUNTIME_ENV_PATH): "sha256:abc"},
    }
    base.update(over)
    return base

def test_is_applyable_accepts_proper():
    ok, why = store.is_applyable(_rec())
    assert ok is True, why

def test_is_applyable_rejects_old_b2_0_revision_no_binding():
    # the user's acceptance requirement: fhb lacking rs-runtime.env → NON-applyable (AE-C1).
    ok, why = store.is_applyable(_rec(file_hashes_before={}))
    assert ok is False and "rs-runtime.env" in why

def test_is_applyable_rejects_stale_schema():
    ok, why = store.is_applyable(_rec(fhb_schema=None))
    assert ok is False and "schema" in why.lower()

def test_is_applyable_rejects_non_validated_status():
    ok, why = store.is_applyable(_rec(status=store.STATUS_APPLIED))
    assert ok is False and "validated" in why

def test_is_applyable_rejects_mixed_impact():
    ok, why = store.is_applyable(_rec(impact=["NEW_SESSIONS_ONLY", "RESTART_ENCODER"]))
    assert ok is False and "NEW_SESSIONS_ONLY" in why


# ── AE-C12: set_status state machine ─────────────────────────────────────────

def test_set_status_transitions_and_persists():
    rid, _ = store.persist_validated({"webrtc": {"ice_policy": "all"}},
                                     type("R", (), {"diff": [], "impact": []})())
    assert store.get_revision(rid)["status"] == store.STATUS_VALIDATED
    assert store.set_status(rid, store.STATUS_APPLYING) is True
    assert store.get_revision(rid)["status"] == store.STATUS_APPLYING
    assert store.set_status(rid, store.STATUS_APPLIED) is True
    assert store.get_revision(rid)["status"] == store.STATUS_APPLIED

def test_set_status_unknown_status_raises():
    with pytest.raises(ValueError):
        store.set_status("rev-x", "bogus")

def test_set_status_unknown_revision_returns_false():
    assert store.set_status("rev-doesnotexist", store.STATUS_APPLIED) is False

def test_persist_stamps_fhb_schema_and_binds(tmp_path, monkeypatch):
    rt = tmp_path / "rs-runtime.env"; rt.write_text("ICE_POLICY=relay\n")
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)
    rid, _ = store.persist_validated({"webrtc": {"ice_policy": "all"}},
                                     type("R", (), {"diff": [], "impact": []})())
    rec = store.get_revision(rid)
    assert rec["fhb_schema"] == store.FHB_SCHEMA
    assert str(rt) in rec["binds"]


# ── AE-C2: the writer uses a SEPARATE lock → no self-deadlock ────────────────

def test_apply_lock_is_distinct_from_env_store_lock(tmp_path):
    p = tmp_path / "rs-runtime.env"
    env_store_lock = str(p) + ".lock"                 # what write_env_atomic would flock
    assert str(apply_lock_path(p)) != env_store_lock  # AE-C2: must differ
    assert str(apply_lock_path(p)) == str(p) + ".apply.lock"

def test_writer_completes_no_self_deadlock(tmp_path):
    # If the writer re-locked the same path (the v1 bug), this would hang. It returns.
    p = tmp_path / "rs-runtime.env"
    h1 = write_runtime_env({"ICE_POLICY": "relay", "TURN_CRED_TTL": "3600"}, env_path=p)
    h2 = write_runtime_env({"ICE_POLICY": "all"}, env_path=p)   # second call also completes
    assert h1.startswith("sha256:") and h2.startswith("sha256:") and h1 != h2


# ── writer integrity: merge, header, mode, foreign/secret keys, drift ────────

def test_writer_merge_preserves_other_allowlisted_key(tmp_path):
    p = tmp_path / "rs-runtime.env"
    write_runtime_env({"ICE_POLICY": "relay", "TURN_CRED_TTL": "3600"}, env_path=p)
    write_runtime_env({"ICE_POLICY": "all"}, env_path=p)   # only change ICE
    body = p.read_text()
    assert "ICE_POLICY=all" in body and "TURN_CRED_TTL=3600" in body   # ttl preserved

def test_writer_reemits_allowlist_header(tmp_path):
    p = tmp_path / "rs-runtime.env"
    write_runtime_env({"ICE_POLICY": "relay"}, env_path=p)
    assert "Allowlist: ICE_POLICY, TURN_CRED_TTL" in p.read_text()   # AE-C7

def test_writer_preserves_mode_0600(tmp_path):
    p = tmp_path / "rs-runtime.env"
    p.write_text("ICE_POLICY=relay\n"); os.chmod(p, 0o600)
    write_runtime_env({"ICE_POLICY": "all"}, env_path=p)
    assert (os.stat(p).st_mode & 0o777) == 0o600   # AE-C6: not widened to 0644

def test_writer_rejects_foreign_update_key(tmp_path):
    p = tmp_path / "rs-runtime.env"
    with pytest.raises(ForeignKeyError):
        write_runtime_env({"EVIL": "x"}, env_path=p)

def test_writer_rejects_secret_in_file_no_writethrough(tmp_path):
    # a hand-added secret in the file → reject BEFORE writing (AE-C8); never world-expose it.
    p = tmp_path / "rs-runtime.env"
    p.write_text("ICE_POLICY=relay\nTURN_SHARED_SECRET=hunter2\n")
    before = p.read_text()
    with pytest.raises(ForeignKeyError):
        write_runtime_env({"ICE_POLICY": "all"}, env_path=p)
    assert p.read_text() == before   # file untouched — no write-through

def test_writer_expected_hash_drift_raises(tmp_path):
    p = tmp_path / "rs-runtime.env"
    write_runtime_env({"ICE_POLICY": "relay"}, env_path=p)
    stale = current_hash(env_path=p)
    write_runtime_env({"ICE_POLICY": "all"}, env_path=p)        # someone else changed it
    with pytest.raises(DriftError):
        write_runtime_env({"ICE_POLICY": "relay"}, env_path=p, expected_hash=stale)   # AE-C19

def test_writer_creates_file_with_default_mode(tmp_path):
    p = tmp_path / "rs-runtime.env"  # absent
    write_runtime_env({"ICE_POLICY": "relay"}, env_path=p)
    assert p.is_file() and (os.stat(p).st_mode & 0o777) == 0o644
