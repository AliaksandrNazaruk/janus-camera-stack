"""B2-0 — runtime-config revision journal + capability report.

Guards the journal safety contract: stable diff hash, no secrets in the journal, NO live-config
mutation from /validate. The capability report's apply_supported tracks the LIVE NEW_SESSIONS_ONLY
applyability (AE-1: POST /apply is live; rollback is internal to apply, not a separate endpoint).
"""
import json

import pytest

from app.config.runtime_schema import ApplyImpact, DiffEntry, ValidationResponse
from app.services import runtime_revision_store as store


def _result(impact=ApplyImpact.NEW_SESSIONS_ONLY):
    return ValidationResponse(
        valid=True,
        diff=[DiffEntry(path="webrtc.ice_policy", **{"from": "all"}, to="relay",
                        source="Settings.ice_policy", impact=impact)],
        impact=[impact],
    )


def _fake_env_path(monkeypatch, path):
    # Settings is frozen — patch get_settings() to return a stub with the env_path we want.
    monkeypatch.setattr("app.core.settings.get_settings",
                        lambda: type("S", (), {"env_path": path})())


# ── diff hash: stable + sensitive to change (C9) ─────────────────────────────

def test_diff_hash_is_stable_for_same_patch():
    patch = {"webrtc": {"ice_policy": "relay"}}
    h1 = store.compute_diff_hash(patch, {})
    h2 = store.compute_diff_hash({"webrtc": {"ice_policy": "relay"}}, {})
    assert h1 == h2 and h1.startswith("sha256:")

def test_diff_hash_changes_with_patch():
    a = store.compute_diff_hash({"webrtc": {"ice_policy": "relay"}}, {})
    b = store.compute_diff_hash({"webrtc": {"ice_policy": "all"}}, {})
    assert a != b

def test_diff_hash_key_order_independent():
    a = store.compute_diff_hash({"webrtc": {"ice_policy": "relay"}, "version": 1}, {})
    b = store.compute_diff_hash({"version": 1, "webrtc": {"ice_policy": "relay"}}, {})
    assert a == b  # canonicalization sorts keys

def test_diff_hash_normalizes_int_float():
    a = store.compute_diff_hash({"stream_profiles": {"S:color": {"fps": 30}}}, {})
    b = store.compute_diff_hash({"stream_profiles": {"S:color": {"fps": 30.0}}}, {})
    assert a == b  # 30 and 30.0 normalize identically

def test_diff_hash_binds_file_hashes():
    p = {"stream_profiles": {"S:color": {"fps": 30}}}
    assert store.compute_diff_hash(p, {}) != store.compute_diff_hash(p, {"/x": "sha256:deadbeef"})


# ── file_hashes_before is READ-ONLY (C4) ─────────────────────────────────────

def test_file_hashes_before_reads_color_tuning(tmp_path, monkeypatch):
    target = tmp_path / "rs-color.tuning.env"
    target.write_text("FPS=15\nBITRATE_KBPS=900\n")
    _fake_env_path(monkeypatch, target)
    fhb = store.file_hashes_before({"stream_profiles": {"141722072135:color": {"fps": 30}}})
    assert str(target) in fhb and fhb[str(target)].startswith("sha256:")
    assert target.read_text() == "FPS=15\nBITRATE_KBPS=900\n"  # NOT mutated

def test_file_hashes_before_no_color_hash_when_no_color(tmp_path, monkeypatch):
    # A webrtc-only patch binds NO color tuning file, but DOES bind rs-runtime.env (AE-C1).
    missing_color = tmp_path / "nope.env"
    _fake_env_path(monkeypatch, missing_color)
    rt = tmp_path / "rs-runtime.env"
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)  # absent → sentinel
    fhb = store.file_hashes_before({"webrtc": {"ice_policy": "relay"}})
    assert str(missing_color) not in fhb and not missing_color.exists()   # no color hash, read-only
    assert fhb == {str(rt): store.RUNTIME_ENV_SENTINEL}                    # rs-runtime.env bound (absent)


# ── persist + read round-trip; no secrets in the journal (C11) ───────────────

def test_persist_and_get_revision_round_trip():
    rid, dhash = store.persist_validated({"webrtc": {"ice_policy": "relay"}}, _result(),
                                         effective_before={"webrtc": {"ice_policy": "all"}})
    assert rid.startswith("rev-") and dhash.startswith("sha256:")
    rec = store.get_revision(rid)
    assert rec["revision_id"] == rid and rec["diff_hash"] == dhash
    assert rec["status"] == "validated" and rec["apply_supported"] is True   # NEW_SESSIONS_ONLY is applyable (AE-1)
    assert rec["impact"] == ["NEW_SESSIONS_ONLY"]

def test_revision_is_secret_redacted():
    rid, _ = store.persist_validated(
        {"webrtc": {"ice_policy": "relay"}}, _result(),
        effective_before={"turn": {"host": "x"}, "leak": {"turn_shared_secret": "S3CRET", "password": "p"}})
    rec = store.get_revision(rid)
    blob = json.dumps(rec).lower()
    assert "s3cret" not in blob and "\"p\"" not in json.dumps(rec)
    assert rec["effective_before"]["leak"]["turn_shared_secret"] == "***REDACTED***"
    assert rec["effective_before"]["leak"]["password"] == "***REDACTED***"
    assert rec["effective_before"]["turn"]["host"] == "x"  # non-secret preserved

def test_get_revision_unknown_returns_none():
    assert store.get_revision("rev-doesnotexist") is None

def test_get_revision_rejects_path_traversal():
    assert store.get_revision("../../etc/passwd") is None
    assert store.get_revision("rev-../escape") is None


# ── retention + durability are journal-only (no live mutation) ───────────────

def test_persist_writes_only_under_revision_dir(monkeypatch, tmp_path):
    # env_path points at a file that must remain untouched
    env = tmp_path / "rs-color.tuning.env"
    env.write_text("FPS=15\n")
    _fake_env_path(monkeypatch, env)
    store.persist_validated({"stream_profiles": {"141722072135:color": {"fps": 30}}}, _result(ApplyImpact.RESTART_ENCODER))
    assert env.read_text() == "FPS=15\n"                       # live config NOT mutated
    assert len(store.list_revisions()) == 1                    # only the journal was written

def test_prune_keeps_last_50(monkeypatch):
    monkeypatch.setattr(store, "MAX_REVISIONS", 5)
    for i in range(8):
        # vary the patch so each diff_hash (and thus filename suffix) differs
        store.persist_validated({"webrtc": {"ice_policy": "relay"}, "n": i}, _result())
    assert len(store.list_revisions()) <= 5


# ── capability report (grounded blockers; apply LIVE for NEW_SESSIONS_ONLY when cleared) ──

def test_capability_report_blocked_before_relocation(monkeypatch, tmp_path):
    # rs-runtime.env absent → C2 blocker present (IaC relocation pending).
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", tmp_path / "rs-runtime.env")
    rep = store.capability_report()
    assert rep["apply_supported"] is False
    assert rep["supported_steps"] == ["journal_only"]
    bi = rep["blocked_impacts"]
    assert set(bi) >= {"NEW_SESSIONS_ONLY", "RESTART_ENCODER", "DEPLOYMENT_ONLY", "REJECTED"}
    assert any("rs-runtime.env" in b for b in bi["NEW_SESSIONS_ONLY"])   # C2 blocker
    assert any("FDIR quiesce" in b for b in bi["RESTART_ENCODER"])

def test_capability_report_c2_clears_after_relocation(monkeypatch, tmp_path):
    # rs-runtime.env present + fields no longer frozen (Track A landed) → NEW_SESSIONS_ONLY is applyable;
    # the B2 apply ENGINE (AE-1) is LIVE → apply_supported True, no NEW_SESSIONS_ONLY blockers.
    f = tmp_path / "rs-runtime.env"
    f.write_text("ICE_POLICY=relay\nTURN_CRED_TTL=3600\n")
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", f)
    rep = store.capability_report()
    assert rep["apply_supported"] is True
    assert rep["blocked_impacts"]["NEW_SESSIONS_ONLY"] == []   # C1 + C2 cleared → no blockers
    assert "apply" in rep["supported_steps"]

def test_ice_policy_no_longer_frozen_literal():
    # Track A step 1 refactored these to field(default_factory=...) → no longer frozen
    # import-time literals (C1 resolved; cache_clear can now refresh them).
    assert store._settings_field_is_frozen_literal("ice_policy") is False
    assert store._settings_field_is_frozen_literal("turn_cred_ttl") is False


# ── endpoints (admin-gated, read-only) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_endpoint_returns_revision_id(admin_client, monkeypatch):
    monkeypatch.setattr("app.routes.runtime_config.validate_patch", lambda patch: _result())
    monkeypatch.setattr("app.routes.runtime_config.build_effective",
                        lambda: type("E", (), {"model_dump": lambda self, **k: {"webrtc": {"ice_policy": "all"}}})())
    resp = await admin_client.post("/api/v1/admin/runtime-config/validate",
                                   json={"webrtc": {"ice_policy": "relay"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision_id"].startswith("rev-") and body["diff_hash"].startswith("sha256:")
    # the journaled revision is retrievable
    rev = await admin_client.get(f"/api/v1/admin/runtime-config/revisions/{body['revision_id']}")
    assert rev.status_code == 200 and rev.json()["diff_hash"] == body["diff_hash"]

@pytest.mark.asyncio
async def test_validate_invalid_patch_no_revision(admin_client, monkeypatch):
    monkeypatch.setattr("app.routes.runtime_config.validate_patch",
                        lambda patch: ValidationResponse(valid=False))
    resp = await admin_client.post("/api/v1/admin/runtime-config/validate", json={"bad": 1})
    assert resp.status_code == 200 and resp.json()["revision_id"] is None

@pytest.mark.asyncio
async def test_capabilities_endpoint_admin_gated(client):
    resp = await client.get("/api/v1/admin/runtime-config/capabilities")
    assert resp.status_code in (401, 403, 503)

@pytest.mark.asyncio
async def test_capabilities_endpoint_reports_state(admin_client, monkeypatch, tmp_path):
    # force the C2 blocker (rs-runtime.env absent) so apply_supported is deterministically False here
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", tmp_path / "absent-rs-runtime.env")
    resp = await admin_client.get("/api/v1/admin/runtime-config/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["apply_supported"] is False                                  # C2 blocked
    assert any("rs-runtime.env" in b for b in body["blocked_impacts"]["NEW_SESSIONS_ONLY"])

@pytest.mark.asyncio
async def test_revisions_endpoint_admin_gated(client):
    resp = await client.get("/api/v1/admin/runtime-config/revisions/rev-x")
    assert resp.status_code in (401, 403, 503)

@pytest.mark.asyncio
async def test_revision_unknown_404(admin_client):
    resp = await admin_client.get("/api/v1/admin/runtime-config/revisions/rev-nope")
    assert resp.status_code == 404


# ── B2-0 must NOT introduce apply/rollback (safety contract) ─────────────────

@pytest.mark.asyncio
async def test_no_separate_rollback_endpoint(admin_client):
    # AE-1 adds /apply (rollback is INTERNAL to apply, not a separate endpoint).
    resp = await admin_client.post("/api/v1/admin/runtime-config/rollback", json={})
    assert resp.status_code in (404, 405)
    # /apply now EXISTS (AE-1) — an empty body is rejected for a missing revision_id, not 404.
    resp = await admin_client.post("/api/v1/admin/runtime-config/apply", json={})
    assert resp.status_code == 422 and resp.status_code not in (404, 405)
