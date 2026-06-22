"""AE-1 — the NEW_SESSIONS_ONLY /apply orchestration. The 12 operator-listed regressions
plus the happy path and endpoint wiring. Orchestration tests call apply_revision() directly
(fast, controllable); build_effective + validate are mocked to faithful lightweight stand-ins.
"""
import os

import pytest

from app.config.runtime_schema import ApplyImpact, ValidationResponse
from app.services import runtime_config_apply as A
from app.services import runtime_env_writer as W
from app.services import runtime_revision_store as store
from app.services.runtime_config_apply import Outcome, apply_revision


# ── faithful lightweight stand-ins ───────────────────────────────────────────

class _WR:
    def __init__(self, ice, ttl): self.ice_policy = ice; self.turn_credential_ttl_seconds = ttl
class _Eff:
    def __init__(self, wr): self.webrtc = wr

def _install_build_effective(monkeypatch, camera_type="color_camera"):
    # Replicates build_effective's webrtc view from os.environ + the depth-camera forced relay.
    def _be():
        ice = os.environ.get("ICE_POLICY", "all")
        if ice not in ("all", "relay"):
            ice = "all"
        if camera_type == "depth_camera":
            ice = "relay"
        ttl = max(300, min(3600, int(os.environ.get("TURN_CRED_TTL", "3600"))))
        return _Eff(_WR(ice, ttl))
    monkeypatch.setattr("app.services.runtime_config_builder.build_effective", _be)

def _install_validate(monkeypatch, valid=True, impact=("NEW_SESSIONS_ONLY",)):
    monkeypatch.setattr("app.services.runtime_config_validator.validate",
                        lambda patch: ValidationResponse(valid=valid, impact=[ApplyImpact(i) for i in impact]))

def _install_camera_type(monkeypatch, camera_type):
    from unittest.mock import MagicMock
    monkeypatch.setattr("app.core.settings.get_settings",
                        MagicMock(return_value=MagicMock(camera_type=camera_type)))


@pytest.fixture(autouse=True)
def _fresh_settings_cache():
    from app.core.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _setup(monkeypatch, tmp_path, seed="ICE_POLICY=relay\nTURN_CRED_TTL=3600\n",
           env=("relay", "3600"), camera_type="color_camera"):
    rt = tmp_path / "rs-runtime.env"
    rt.write_text(seed)
    monkeypatch.setattr(store, "RUNTIME_ENV_PATH", rt)
    monkeypatch.setattr(W, "RUNTIME_ENV_PATH", rt)
    if env[0] is not None:
        monkeypatch.setenv("ICE_POLICY", env[0])
    else:
        monkeypatch.delenv("ICE_POLICY", raising=False)
    if env[1] is not None:
        monkeypatch.setenv("TURN_CRED_TTL", env[1])
    else:
        monkeypatch.delenv("TURN_CRED_TTL", raising=False)
    if camera_type == "depth_camera":
        _install_camera_type(monkeypatch, "depth_camera")
    _install_build_effective(monkeypatch, camera_type)
    _install_validate(monkeypatch)
    return rt

def _journal(rt, patch, *, impact=("NEW_SESSIONS_ONLY",), bind=True, status="validated"):
    fhb = store.file_hashes_before(patch) if bind else {}
    diff_hash = store.compute_diff_hash(patch, fhb)
    rid = f"rev-testae1-{diff_hash[7:15]}"
    rec = {"revision_id": rid, "diff_hash": diff_hash, "validated_patch": patch,
           "impact": list(impact), "file_hashes_before": fhb,
           "fhb_schema": store.FHB_SCHEMA if bind else None, "binds": sorted(fhb.keys()),
           "status": status, "apply_supported": False}
    store._atomic_write_json(store.REVISION_DIR / f"{rid}.json", rec)
    return rid, diff_hash


# ── the 12 ───────────────────────────────────────────────────────────────────

def test_01_old_revision_no_binding_rejected(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}}, bind=False)
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.REJECTED and "rs-runtime.env" in r.detail   # 422

def test_02_wrong_confirm_400(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rid, dh = _journal(store.RUNTIME_ENV_PATH, {"webrtc": {"ice_policy": "all"}})
    r = apply_revision(rid, "apply-sha256:WRONG")
    assert r.outcome == Outcome.CONFIRM_MISMATCH   # 400

def test_03_file_drift_409(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    # byte change that keeps keys coherent with os.environ → base-match (DRIFT), not coherence.
    rt.write_text("# edited after validate\nICE_POLICY=relay\nTURN_CRED_TTL=3600\n")
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.DRIFT   # 409 (base hash != stored)

def test_03b_coherence_conflict_409(monkeypatch, tmp_path):
    # a disk/process divergence (post-rollback_failed style) → 409 CONFLICT, caught before write.
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    rt.write_text("ICE_POLICY=relay\nTURN_CRED_TTL=1234\n")   # disk ttl != os.environ ttl (3600)
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.CONFLICT   # 409 (coherence)

def test_04_mixed_impact_rejected(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}}, impact=("NEW_SESSIONS_ONLY", "RESTART_ENCODER"))
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.REJECTED and "NEW_SESSIONS_ONLY" in r.detail   # 422

def test_05_concurrent_apply_423(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    with W.runtime_env_lock(rt):                 # hold the apply lock
        r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.LOCK_HELD         # 423

def test_06_write_failure_no_live_change(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    monkeypatch.setattr(W, "write_locked", lambda *a, **k: (_ for _ in ()).throw(OSError("EROFS")))
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.WRITE_FAILED                      # 500
    assert os.environ["ICE_POLICY"] == "relay"                   # os.environ UNCHANGED
    assert store.get_revision(rid)["status"] == store.STATUS_VALIDATED   # status restored

def test_07_verify_failure_rollback(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    # build_effective always returns relay → verify of target=all never passes → rollback
    monkeypatch.setattr("app.services.runtime_config_builder.build_effective", lambda: _Eff(_WR("relay", 3600)))
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.ROLLED_BACK                       # 500
    assert os.environ["ICE_POLICY"] == "relay"                   # restored
    assert "ICE_POLICY=relay" in rt.read_text()                  # file restored

def test_08_rollback_to_unset(monkeypatch, tmp_path):
    # TURN_CRED_TTL absent before; apply introduces it; rollback must DELETE it (AE-C3).
    rt = _setup(monkeypatch, tmp_path, seed="ICE_POLICY=relay\n", env=("relay", None))
    rid, dh = _journal(rt, {"webrtc": {"turn_credential_ttl_seconds": 1800}})
    monkeypatch.setattr("app.services.runtime_config_builder.build_effective", lambda: _Eff(_WR("relay", 3600)))
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.ROLLED_BACK
    assert "TURN_CRED_TTL" not in os.environ                     # deleted, not "None"
    assert "TURN_CRED_TTL" not in rt.read_text()                 # file restored to prior (no ttl)

def test_09_rollback_file_failure_rollback_failed(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    monkeypatch.setattr("app.services.runtime_config_builder.build_effective", lambda: _Eff(_WR("relay", 3600)))
    monkeypatch.setattr(W, "restore_locked", lambda *a, **k: (_ for _ in ()).throw(OSError("EROFS")))
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.ROLLBACK_FAILED                  # 500
    assert store.get_revision(rid)["status"] == store.STATUS_ROLLBACK_FAILED

def test_10_depth_ice_policy_no_false_rollback(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path, camera_type="depth_camera")
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.APPLIED                          # NOT rolled back (AE-C10)

def test_11_idempotent_already_equal(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)   # current ice=relay
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "relay"}})   # apply relay == current
    before = rt.read_text()
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.APPLIED and r.changed is False   # no-op
    assert rt.read_text() == before                              # no write

def test_12_happy_path_applies_and_persists(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}})
    r = apply_revision(rid, f"apply-{dh}")
    assert r.outcome == Outcome.APPLIED and r.changed is True and r.verified is True
    assert "ICE_POLICY=all" in rt.read_text() and os.environ["ICE_POLICY"] == "all"
    assert store.get_revision(rid)["status"] == store.STATUS_APPLIED


# ── recover_on_boot ──────────────────────────────────────────────────────────

def test_recover_on_boot_reconciles_applying(monkeypatch, tmp_path):
    rt = _setup(monkeypatch, tmp_path)
    rid, dh = _journal(rt, {"webrtc": {"ice_policy": "all"}}, status=store.STATUS_APPLYING)
    # disk == base (write never landed) → rolled_back
    n = A.recover_on_boot()
    assert n == 1 and store.get_revision(rid)["status"] == store.STATUS_ROLLED_BACK


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_endpoint_admin_gated(client):
    resp = await client.post("/api/v1/admin/runtime-config/apply", json={"revision_id": "rev-x", "confirm": "y"})
    assert resp.status_code in (401, 403, 503)

@pytest.mark.asyncio
async def test_apply_endpoint_maps_outcome_to_http(admin_client, monkeypatch):
    from app.services.runtime_config_apply import ApplyResult
    monkeypatch.setattr("app.routes.runtime_config.apply_revision",
                        lambda rid, confirm: ApplyResult(Outcome.APPLIED, rid, changed=True, verified=True))
    resp = await admin_client.post("/api/v1/admin/runtime-config/apply",
                                   json={"revision_id": "rev-abc", "confirm": "apply-sha256:x"})
    assert resp.status_code == 200 and resp.json()["status"] == "applied" and resp.json()["changed"] is True

@pytest.mark.asyncio
async def test_apply_endpoint_drift_409(admin_client, monkeypatch):
    from app.services.runtime_config_apply import ApplyResult
    monkeypatch.setattr("app.routes.runtime_config.apply_revision",
                        lambda rid, confirm: ApplyResult(Outcome.DRIFT, rid, detail="drift"))
    resp = await admin_client.post("/api/v1/admin/runtime-config/apply",
                                   json={"revision_id": "rev-abc", "confirm": "apply-x"})
    assert resp.status_code == 409 and resp.json()["status"] == "drift"
