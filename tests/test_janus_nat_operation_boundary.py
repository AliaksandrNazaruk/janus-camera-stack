"""Characterization tests for the Janus NAT/TURN update operation boundary.

Cycle 7B — these tests now pin the EXTRACTED operation
(``app.application.janus_nat.update_nat_config``; the route is a thin adapter). The 7A
characterization froze the old inline behaviour; this commit re-pointed them DELIBERATELY to the
new stage/result model — the git diff is the audit trail for the error-model change.

What the 7B model guarantees (see docs/design/JANUS_NAT_OPERATION_BOUNDARY.md):
  * Stages, in order: persist → patch jcfg (``no_restart=True``) → restart local → restart depth.
  * G7 closed — NO double restart: patch runs with ``--no-restart``; ONE explicit ``restart_janus``.
  * G3 closed — uniform mapping: ``restart_janus`` now wraps ``TimeoutExpired``/``FileNotFoundError``
    into ``JanusAdminError`` (RuntimeError) like patch does → nothing escapes the operation unmapped.
  * G4/G6 closed — a stage failure returns 500 with a STRUCTURED body
    (``failure_stage`` + ``desired_persisted`` / ``local_applied`` / ``local_restarted`` /
    ``depth_restarted`` + the L3 ``exit_code``).
  * G2 closed — the depth restart is BEST EFFORT: local-success + depth-fail ⇒ 200 with ``warnings``.
  * persist OSError is now CAUGHT (``failure_stage="persist"``), not an unmapped escape.
  * keep-password (``turn_pwd in ("", "***")`` reuses the stored secret) is preserved in the use-case.
  * (G1 persist-before-apply: ``save`` still runs first in 7B.1 — the staged desired/applied store
    status that fully closes G1 lands in 7B.2.)
"""
from __future__ import annotations

import subprocess

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock, patch

from app.application.janus_nat import update_nat_config as run_update
from app.routes.janus import JanusNatConfig
from app.services import nat_config
from app.services.nat_config import load_nat_config, patch_janus_cfg_with_nat

# Matches conftest._TEST_TOKEN (kept local to avoid importing conftest as a module).
_TOKEN = "test-token-conftest-default"

# Cycle 7B: the operation lives in app/application/janus_nat/update_nat_config, which calls the
# building blocks module-qualified as nat_config.<fn> — so we mock them at the nat_config SOURCE
# (patch-at-the-source). The route is now a thin adapter that maps NatUpdateResult → HTTP.
_SAVE = "app.services.nat_config.save_nat_config"
_PATCH = "app.services.nat_config.patch_janus_cfg_with_nat"
_RESTART_LOCAL = "app.services.nat_config.restart_janus"
_RESTART_DEPTH = "app.services.nat_config.restart_depth_camera_janus"
_LOAD = "app.services.nat_config.load_nat_config"

# nat_config delegates to this single CLI for both patch and restart.
_NAT_CFG_RUN = "app.services.nat_config.subprocess.run"


def _admin_ac(app, raise_app_exceptions: bool = True) -> AsyncClient:
    """Admin-authenticated ASGI client.

    ``raise_app_exceptions=True`` lets *unmapped* exceptions propagate into the
    test (proving the handler does not catch them); FastAPI would turn them into
    a bare 500 in production.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Admin-Token": _TOKEN},
    )


# ── 1. Happy path ──────────────────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_stages_run_in_order_and_response_is_masked(self, app):
        mgr = MagicMock()
        with patch(_SAVE, mgr.save), patch(_PATCH, mgr.patch), patch(
            _RESTART_LOCAL, mgr.restart_local
        ), patch(_RESTART_DEPTH, mgr.restart_depth):
            async with _admin_ac(app) as ac:
                # explicit (non-"" / non-"***") pwd → keep-password branch skipped
                resp = await ac.post("/janus/nat", json={"turn_pwd": "supersecretpw"})

        assert resp.status_code == 200
        assert resp.json()["turn_pwd"] == "***"  # never echoed back
        assert [c[0] for c in mgr.mock_calls] == [
            "save",
            "patch",
            "restart_local",
            "restart_depth",
        ]
        # G7: patch runs with no_restart so it does NOT restart — the single restart is restart_local.
        assert mgr.patch.call_args.kwargs.get("no_restart") is True


# ── 2. keep-password (branch behaviour) ────────────────────────────────


class TestKeepPassword:
    @pytest.mark.asyncio
    async def test_masked_pwd_reuses_stored_secret(self, app):
        """POST turn_pwd="***" must not clobber the stored secret with the mask;
        the route reloads and substitutes the stored password before persisting."""
        save = MagicMock()
        with patch(_LOAD, return_value=JanusNatConfig(turn_pwd="STORED_SECRET")), patch(
            _SAVE, save
        ), patch(_PATCH), patch(_RESTART_LOCAL), patch(_RESTART_DEPTH):
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "***"})

        assert resp.status_code == 200
        persisted = save.call_args.args[0]
        assert persisted.turn_pwd == "STORED_SECRET"  # mask resolved to stored secret
        assert resp.json()["turn_pwd"] == "***"


# ── 3. persist_desired stage ───────────────────────────────────────────


class TestPersistStage:
    @pytest.mark.asyncio
    async def test_save_oserror_is_caught_as_persist_failure_stage(self, app):
        """7B: the use-case CATCHES a persist OSError → 500 with failure_stage='persist',
        desired_persisted=False, and apply never starts (was an unmapped escape in 7A)."""
        with patch(_SAVE, side_effect=OSError("disk full")), patch(_PATCH) as p, patch(
            _RESTART_LOCAL
        ), patch(_RESTART_DEPTH):
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})
            assert resp.status_code == 500
            body = resp.json()
            assert body["failure_stage"] == "persist"
            assert body["desired_persisted"] is False
            p.assert_not_called()  # apply never started


# ── 4. patch_jcfg stage (janus-admin CLI, L3 boundary) ─────────────────


class TestPatchStage:
    @pytest.mark.asyncio
    async def test_patch_failure_returns_structured_500(self, app):
        """7B: a patch JanusAdminError → 500 with failure_stage='patch_local', local_applied=False,
        the L3 exit_code surfaced, and restart never reached. (desired is still persisted first in
        7B.1 — save called once; the staged status that closes G1 lands in 7B.2.)"""
        from app.services.nat_config import JanusAdminError
        with patch(_SAVE) as save, patch(
            _PATCH, side_effect=JanusAdminError("janus-admin nat-config exit=3", exit_code=3)
        ), patch(_RESTART_LOCAL) as rl, patch(_RESTART_DEPTH) as rd:
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})

        assert resp.status_code == 500
        body = resp.json()
        assert body["failure_stage"] == "patch_local"
        assert body["local_applied"] is False and body["desired_persisted"] is True
        assert body["exit_code"] == 3                # G6: L3 exit code surfaced (was collapsed in 7A)
        assert "janus-admin" in body["detail"]
        save.assert_called_once()
        rl.assert_not_called()
        rd.assert_not_called()

    def test_patch_invokes_janus_admin_cli_with_json_payload(self):
        """L3 boundary: L4 ships the config as JSON to the janus-admin CLI;
        it no longer touches janus.jcfg directly."""
        run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
        with patch(_NAT_CFG_RUN, run):
            patch_janus_cfg_with_nat(JanusNatConfig())
        args, kwargs = run.call_args
        assert args[0] == ["sudo", "/usr/local/bin/janus-admin", "nat-config"]
        assert "stun_server" in kwargs["input"]  # JSON payload on stdin

    @pytest.mark.parametrize(
        "exc",
        [
            subprocess.TimeoutExpired(cmd="janus-admin", timeout=120),
            FileNotFoundError("janus-admin missing"),
        ],
    )
    def test_patch_maps_timeout_and_missing_binary_to_runtimeerror(self, exc):
        """Unlike restart_janus, patch DEFENDS against a hung/absent CLI: both
        TimeoutExpired and FileNotFoundError are wrapped into RuntimeError."""
        with patch(_NAT_CFG_RUN, side_effect=exc):
            with pytest.raises(RuntimeError):
                patch_janus_cfg_with_nat(JanusNatConfig())


# ── 5. restart stages (the asymmetry) ──────────────────────────────────


class TestRestartStages:
    @pytest.mark.asyncio
    async def test_local_restart_failure_returns_structured_500(self, app):
        """7B: restart_local JanusAdminError → 500 with failure_stage='restart_local',
        local_applied=True, local_restarted=False; depth never reached."""
        from app.services.nat_config import JanusAdminError
        with patch(_SAVE), patch(_PATCH) as p, patch(
            _RESTART_LOCAL, side_effect=JanusAdminError("janus-admin restart exit=4", exit_code=4)
        ), patch(_RESTART_DEPTH) as rd:
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})

        assert resp.status_code == 500
        body = resp.json()
        assert body["failure_stage"] == "restart_local"
        assert body["local_applied"] is True and body["local_restarted"] is False
        p.assert_called_once()  # patch already applied via L3
        rd.assert_not_called()

    @pytest.mark.parametrize(
        "exc",
        [
            subprocess.TimeoutExpired(cmd="janus-admin", timeout=120),
            FileNotFoundError("janus-admin missing"),
        ],
    )
    def test_restart_janus_maps_timeout_and_missing_binary(self, exc):
        """G3 closed: the REAL restart_janus now wraps TimeoutExpired / FileNotFoundError into
        JanusAdminError (a RuntimeError), symmetric with patch — so the operation catches them as a
        restart_local failure instead of letting them escape unmapped (the 7A asymmetry)."""
        from app.services.nat_config import JanusAdminError, restart_janus
        with patch(_NAT_CFG_RUN, side_effect=exc):
            with pytest.raises(JanusAdminError):
                restart_janus()

    @pytest.mark.asyncio
    async def test_depth_restart_failure_is_best_effort_200_with_warning(self, app):
        """7B/D3: local applied + restarted, depth restart fails → the operation SUCCEEDS (200) with a
        warning and depth_restarted=False (no rollback; local is already live). No more split-state 500."""
        with patch(_SAVE), patch(_PATCH), patch(_RESTART_LOCAL) as rl, patch(
            _RESTART_DEPTH, side_effect=RuntimeError("Failed to restart janus: 500")
        ):
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["turn_pwd"] == "***"                    # success body = masked config
        assert any("depth" in w for w in body["warnings"])  # best-effort failure surfaced as a warning
        rl.assert_called_once()  # ← local WAS applied; no rollback exists


# ── 6. Error shape ─────────────────────────────────────────────────────


class TestErrorShape:
    @pytest.mark.asyncio
    async def test_error_body_carries_failure_stage_and_applied_flags(self, app):
        """7B/G4 closed: a stage failure (here restart_local) returns a STRUCTURED 500 body with
        failure_stage + the applied-flags + exit_code (was {detail}-only in 7A)."""
        from app.services.nat_config import JanusAdminError
        with patch(_SAVE), patch(_PATCH), patch(
            _RESTART_LOCAL, side_effect=JanusAdminError("boom", exit_code=4)
        ), patch(_RESTART_DEPTH) as rd:
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})

        assert resp.status_code == 500
        body = resp.json()
        assert body["failure_stage"] == "restart_local"
        for key in ("desired_persisted", "local_applied", "local_restarted", "depth_restarted",
                    "exit_code", "detail"):
            assert key in body
        assert body["local_applied"] is True and body["local_restarted"] is False
        rd.assert_not_called()


# ── 7. Registration mode (color-only) ──────────────────────────────────


class TestRegistrationMode:
    @pytest.mark.asyncio
    async def test_post_janus_nat_registered_for_color_camera(self, app):
        """POST /janus/nat exists under the default (color_camera) app. The
        depth-camera omission (janus.py gate) is documented in the design note;
        not reloaded here to keep session state clean."""
        posts = {
            r.path
            for r in app.routes
            if getattr(r, "path", None) == "/janus/nat"
            and "POST" in (getattr(r, "methods", set()) or set())
        }
        assert "/janus/nat" in posts


# ── 8. Read path: silent depth fallback ────────────────────────────────


class TestDepthReadFallback:
    @patch("app.services.nat_config.httpx.get", side_effect=httpx.HTTPError("unreachable"))
    @patch("app.services.nat_config._janus_nat_json")
    @patch("app.services.nat_config.get_settings")
    def test_depth_load_silently_falls_back_to_defaults(self, mock_settings, mock_path, _mock_get):
        """A depth node whose color peer is unreachable does NOT error — it
        silently returns baked-in defaults, which can mask node divergence."""
        mock_settings.return_value = MagicMock(camera_type="depth_camera")
        mock_path.return_value.exists.return_value = False

        cfg = load_nat_config()  # must not raise

        assert isinstance(cfg, JanusNatConfig)
        assert cfg.stun_port == 3478  # baked default, no error surfaced


# ── 9. Apply-status sidecar (Cycle 7B.2 — G1: desired≠applied is now VISIBLE) ──


class TestApplyStatus:
    def test_success_marks_status_applied(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nat_config, "_janus_nat_json", lambda: tmp_path / "janus-nat.json")
        monkeypatch.setattr(nat_config, "patch_janus_cfg_with_nat", lambda cfg, **k: None)
        monkeypatch.setattr(nat_config, "restart_janus", lambda: None)
        monkeypatch.setattr(nat_config, "restart_depth_camera_janus", lambda: None)
        result = run_update(JanusNatConfig(turn_pwd="x"))
        assert result.ok
        st = nat_config.read_apply_status()
        assert st["status"] == "applied" and st["failure_stage"] is None
        assert st["diff_hash"] == nat_config.config_diff_hash(result.config)  # bound to this config

    def test_patch_failure_marks_status_failed_with_stage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nat_config, "_janus_nat_json", lambda: tmp_path / "janus-nat.json")

        def _boom(cfg, **k):
            raise nat_config.JanusAdminError("janus-admin nat-config exit=3", exit_code=3)
        monkeypatch.setattr(nat_config, "patch_janus_cfg_with_nat", _boom)
        result = run_update(JanusNatConfig(turn_pwd="x"))
        assert not result.ok and result.failure_stage == "patch_local"
        st = nat_config.read_apply_status()
        assert st["status"] == "failed" and st["failure_stage"] == "patch_local"   # drift now VISIBLE

    def test_read_status_missing_is_unknown_failsafe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nat_config, "_janus_nat_json", lambda: tmp_path / "absent.json")
        assert nat_config.read_apply_status()["status"] == "unknown"   # no sidecar → unknown, never raises


class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_get_status_returns_sidecar_with_canonical_word(self, app):
        sample = {"status": "applied", "diff_hash": "sha256:abc",
                  "failure_stage": None, "updated_at": 1.0}
        with patch("app.routes.janus.read_apply_status", return_value=sample):
            async with _admin_ac(app) as ac:
                resp = await ac.get("/janus/nat/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"                  # domain status preserved
        assert body["operation_status"] == "succeeded"      # Cycle 8B canonical projection


class TestCanonicalOperationStatus:
    def test_result_exposes_canonical_status(self):
        from app.application.operations import OperationStatus
        from app.application.janus_nat.update_nat_config import NatUpdateResult
        ok = NatUpdateResult(ok=True, config=JanusNatConfig())
        bad = NatUpdateResult(ok=False, config=JanusNatConfig(), failure_stage="restart_local")
        assert ok.operation_status is OperationStatus.SUCCEEDED
        assert bad.operation_status is OperationStatus.FAILED

    @pytest.mark.asyncio
    async def test_failure_body_carries_canonical_status(self, app):
        from app.services.nat_config import JanusAdminError
        with patch(_SAVE), patch(_PATCH), patch(
            _RESTART_LOCAL, side_effect=JanusAdminError("boom", exit_code=4)
        ), patch(_RESTART_DEPTH):
            async with _admin_ac(app) as ac:
                resp = await ac.post("/janus/nat", json={"turn_pwd": "x"})
        assert resp.status_code == 500
        assert resp.json()["operation_status"] == "failed"
