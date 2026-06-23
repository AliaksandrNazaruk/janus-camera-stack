"""A1 — production fail-closed security checks.

Development (default): permissive, no startup abort.
Production (CAMERA_ENV=production): abort on insecure/broken config.
"""
from types import SimpleNamespace

import pytest

from app.core import viewer_auth
from app.core import startup_checks as sc


def _ok_settings(**over):
    base = dict(
        janus_url="http://janus:8088/janus",
        turn_pass="",
        turn_shared_secret="shared-secret",
        turn_host="turn.example.com",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _configure_secure(monkeypatch):
    monkeypatch.setenv("CAM_ADMIN_TOKEN","a" * 20)
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", ["viewer-token-aaaaaaaaaaaa"])


def test_dev_mode_is_noop(monkeypatch):
    # Even with the worst config, development must not abort startup.
    monkeypatch.delenv("CAMERA_ENV", raising=False)
    monkeypatch.setenv("CAM_ADMIN_TOKEN","change-me")
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", [])
    sc.enforce_production_security(_ok_settings())  # must not raise


def test_prod_empty_viewer_tokens_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    monkeypatch.setenv("CAM_ADMIN_TOKEN","a" * 20)
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", [])
    with pytest.raises(RuntimeError, match="VIEWER_TOKENS"):
        sc.enforce_production_security(_ok_settings())


def test_prod_change_me_admin_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    monkeypatch.setenv("CAM_ADMIN_TOKEN","change-me")
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", ["x" * 20])
    with pytest.raises(RuntimeError, match="change-me"):
        sc.enforce_production_security(_ok_settings())


def test_prod_short_admin_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    monkeypatch.setenv("CAM_ADMIN_TOKEN","short")
    monkeypatch.setattr(viewer_auth, "VIEWER_TOKENS", ["x" * 20])
    with pytest.raises(RuntimeError, match="too short"):
        sc.enforce_production_security(_ok_settings())


def test_prod_no_turn_creds_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    with pytest.raises(RuntimeError, match="TURN credentials"):
        sc.enforce_production_security(_ok_settings(turn_pass="", turn_shared_secret=""))


def test_prod_empty_turn_host_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    with pytest.raises(RuntimeError, match="TURN_HOST is empty"):
        sc.enforce_production_security(_ok_settings(turn_host=""))


def test_prod_private_turn_host_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    with pytest.raises(RuntimeError, match="private/loopback"):
        sc.enforce_production_security(_ok_settings(turn_host="192.168.1.10"))


def test_prod_public_turn_host_passes(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    import app.config
    monkeypatch.setattr(app.config, "DEVICES", SimpleNamespace(HOST_LAN_IP="192.168.1.10"))
    # public IP and DNS name both acceptable
    assert "TURN_HOST" not in " ".join(
        sc.production_issues(_ok_settings(turn_host="turn-public.example.org")))
    assert "TURN_HOST" not in " ".join(
        sc.production_issues(_ok_settings(turn_host="turn.example.com")))


def test_prod_localhost_janus_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    with pytest.raises(RuntimeError, match="localhost"):
        sc.enforce_production_security(_ok_settings(janus_url="http://127.0.0.1:8088/janus"))


def test_prod_loopback_host_lan_ip_aborts(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    import app.config
    monkeypatch.setattr(app.config, "DEVICES",
                        SimpleNamespace(HOST_LAN_IP="127.0.0.1"))
    with pytest.raises(RuntimeError, match="HOST_LAN_IP"):
        sc.enforce_production_security(_ok_settings())


def test_prod_passes_when_fully_configured(monkeypatch):
    monkeypatch.setenv("CAMERA_ENV", "production")
    _configure_secure(monkeypatch)
    import app.config
    monkeypatch.setattr(app.config, "DEVICES",
                        SimpleNamespace(HOST_LAN_IP="192.168.1.10"))
    sc.enforce_production_security(_ok_settings())  # must not raise
    assert sc.production_issues(_ok_settings()) == []
