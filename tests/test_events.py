"""Tests for the application lifecycle event handlers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSdNotify:
    """Tests for systemd sd_notify helper."""

    def test_sd_notify_noop_when_no_socket(self):
        from app.core.events import _sd_notify
        with patch.dict(os.environ, {}, clear=False):
            with patch("app.core.events._NOTIFY_SOCKET", None):
                # Should not raise
                _sd_notify("READY=1")

    def test_sd_notify_sends_datagram_when_socket_set(self):
        from app.core.events import _sd_notify
        with patch("app.core.events._NOTIFY_SOCKET", "/tmp/test-sd-notify.sock"), \
             patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            _sd_notify("READY=1")
            mock_sock.sendall.assert_called_once_with(b"READY=1")


class TestRegisterEventHandlers:
    """Tests for startup/shutdown handler registration."""

    def test_register_sets_lifespan_context(self):
        """register_event_handlers wires _lifespan as the app's lifespan_context — the seam that
        replaced @app.on_event (core/app.py calls it; conftest patches it by name)."""
        from app.core.events import _lifespan, register_event_handlers
        mock_app = MagicMock()
        register_event_handlers(mock_app)
        assert mock_app.router.lifespan_context is _lifespan

    @pytest.mark.asyncio
    async def test_startup_starts_watchdogs(self):
        """The lifespan startup sequence (before yield) starts the watchdogs + proxies and READY=1s.
        Same assertions as the pre-lifespan handler test, now driving the context manager."""
        from app.core.events import register_event_handlers
        from app.services import task_registry
        task_registry._reset_for_tests()

        mock_app = MagicMock()

        def fake_create_task(coro, *args, **kwargs):
            m = MagicMock()
            coro.close()  # do not actually run the loop in the test
            return m

        with patch("app.core.events.watchdogs") as mock_watchdogs, \
             patch("app.core.events.start_thermal_monitor") as mock_thermal, \
             patch("app.core.events.janus_proxy") as mock_janus_proxy, \
             patch("app.core.events.relay_proxy") as mock_relay_proxy, \
             patch("app.core.events._sd_notify") as mock_notify, \
             patch("app.core.events.get_settings") as mock_settings, \
             patch("asyncio.create_task", side_effect=fake_create_task), \
             patch("asyncio.gather", new=AsyncMock()), \
             patch("app.services.janus._executor"):

            mock_settings.return_value = MagicMock(camera_type="color_camera")
            mock_watchdogs.start_snapshot_watchdog = AsyncMock()
            mock_janus_proxy.start_client = AsyncMock()
            mock_janus_proxy.stop_client = AsyncMock()
            mock_relay_proxy.start_client = AsyncMock()
            mock_relay_proxy.stop_client = AsyncMock()

            register_event_handlers(mock_app)
            lifespan = mock_app.router.lifespan_context

            async with lifespan(mock_app):
                # past the yield → startup completed
                mock_watchdogs.start_janus_watchdog.assert_called_once()
                mock_watchdogs.start_snapshot_watchdog.assert_awaited_once()
                mock_thermal.assert_called_once()
                mock_janus_proxy.start_client.assert_awaited_once()
                mock_relay_proxy.start_client.assert_awaited_once()
                mock_notify.assert_called_with("READY=1")
        task_registry._reset_for_tests()

    @pytest.mark.asyncio
    async def test_shutdown_stops_proxies_and_cancels_loops(self):
        """The lifespan shutdown (after yield) stops the proxy clients AND, via one
        task_registry.shutdown(), cancels EVERY long-lived async task the registry owns — the three
        loops AND the boot reconcile, which was fire-and-forget (untracked, never cancelled) before
        Cycle 4 (the leak fix)."""
        from app.core.events import register_event_handlers
        from app.services import task_registry
        task_registry._reset_for_tests()

        mock_app = MagicMock()
        created = {}

        def fake_create_task(coro, *args, **kwargs):
            created[coro.cr_code.co_name] = MagicMock()
            coro.close()  # do not actually run the loop in the test
            return created[coro.cr_code.co_name]

        with patch("app.core.events.watchdogs") as mock_watchdogs, \
             patch("app.core.events.start_thermal_monitor"), \
             patch("app.core.events.janus_proxy") as mock_janus_proxy, \
             patch("app.core.events.relay_proxy") as mock_relay_proxy, \
             patch("app.core.events._sd_notify"), \
             patch("app.core.events.get_settings") as mock_settings, \
             patch("asyncio.create_task", side_effect=fake_create_task), \
             patch("asyncio.gather", new=AsyncMock()), \
             patch("app.services.janus._executor"):

            mock_settings.return_value = MagicMock(camera_type="color_camera")
            mock_watchdogs.start_snapshot_watchdog = AsyncMock()
            mock_janus_proxy.start_client = AsyncMock()
            mock_janus_proxy.stop_client = AsyncMock()
            mock_relay_proxy.start_client = AsyncMock()
            mock_relay_proxy.stop_client = AsyncMock()

            register_event_handlers(mock_app)
            lifespan = mock_app.router.lifespan_context

            async with lifespan(mock_app):
                pass  # enter → startup, exit → shutdown

            mock_janus_proxy.stop_client.assert_awaited_once()
            mock_relay_proxy.stop_client.assert_awaited_once()
            # Cycle 4: every long-lived task — incl. the boot reconcile that was fire-and-forget
            # before — is owned by the registry and cancelled on shutdown.
            for name in ("_watchdog_loop", "_memory_gauge_loop", "_mux_fps_scraper",
                         "_reconcile_janus_bg"):
                created[name].cancel.assert_called_once()
        task_registry._reset_for_tests()
