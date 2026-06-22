"""Tests for app/services/system.py — subprocess wrappers."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.system import run, service_restart, service_status, systemd_brief


class TestRun:
    @patch("app.services.system.subprocess.run")
    def test_success(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout="output\n", stderr="")
        result = run(["echo", "hi"])
        assert result == "output\n"

    @patch("app.services.system.subprocess.run")
    def test_failure_raises(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(RuntimeError, match="cmd failed"):
            run(["false"])

    @patch("app.services.system.subprocess.run", side_effect=subprocess.TimeoutExpired(["cmd"], 5))
    def test_timeout(self, mock_sub):
        with pytest.raises(subprocess.TimeoutExpired):
            run(["sleep", "999"])


class TestServiceRestart:
    @patch("app.services.system.run")
    def test_calls_encoder_admin(self, mock_run):
        # service_restart() goes through the L2 encoder-admin CLI (not raw systemctl) —
        # boundary contract (L4 does not shell out to systemctl unit names directly).
        service_restart()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "encoder-admin" in args[1]
        assert "restart" in args


class TestServiceStatus:
    # service_status() parses JSON from encoder-admin status (L2 CLI refactor),
    # not systemctl is-active text.
    @patch("app.services.system.run",
           return_value='{"unit": "rs-stream@color.service", "active": true}')
    def test_active(self, mock_run):
        result = service_status()
        assert result["active"] is True

    @patch("app.services.system.run",
           return_value='{"unit": "rs-stream@color.service", "active": false}')
    def test_inactive(self, mock_run):
        result = service_status()
        assert result["active"] is False


class TestSystemdBrief:
    @patch("app.services.system.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = (
            "ActiveState=active\nActiveEnterTimestamp=Mon 2025-01-01 12:00:00 UTC\nNRestarts=3\n"
        )
        result = systemd_brief()
        assert result["active"] is True
        assert result["restarts"] == 3
        assert "2025" in result["since"]
