import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.settings import get_settings
from app.services import janus_proxy, relay_proxy, task_registry, watchdogs
from app.services.thermal import start_thermal_monitor, stop_thermal_monitor

_log = logging.getLogger("events")

# ── systemd sd_notify via raw socket (no C dependency) ──────────
_NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")


def _sd_notify(state: str) -> None:
    """Send a sd_notify datagram if running under systemd."""
    if not _NOTIFY_SOCKET:
        return
    addr = _NOTIFY_SOCKET
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode())
    except OSError:
        _log.debug("sd_notify(%s) failed", state)


async def _watchdog_loop() -> None:
    """Periodically send WATCHDOG=1 keepalive to systemd."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec or not _NOTIFY_SOCKET:
        return
    interval = int(usec) / 1_000_000 / 2  # half the timeout
    while True:
        _sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


async def _memory_gauge_loop():
    """Periodic RSS memory gauge update."""
    import resource
    try:
        from app.metrics import process_memory_bytes
    except Exception:
        return
    while True:
        try:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # KB -> bytes on Linux
            process_memory_bytes.set(rss)
        except Exception:
            pass
        await asyncio.sleep(30)


async def _mux_fps_scraper():
    """Phase 1 P1-OBS-001: poll realsense-mux:8000/stats every 5sec, push
    per-sensor FPS as a Prometheus gauge. Mux process may not run (color-only
    operation), in which case silent skip — gauge stays at last seen value
    or 0 if never set.
    """
    import httpx
    try:
        from app.metrics import mux_input_fps
    except Exception:
        return
    mux_url = "http://127.0.0.1:8000/stats"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            try:
                r = await client.get(mux_url)
                if r.status_code == 200:
                    data = r.json()
                    for sensor, fps in (data.get("fps") or {}).items():
                        if isinstance(fps, (int, float)):
                            mux_input_fps.labels(sensor=sensor).set(float(fps))
            except (httpx.RequestError, ValueError):
                # Mux not running or returned non-JSON — leave gauges as-is
                pass
            except Exception:
                _log.debug("mux fps scraper iteration failed", exc_info=True)
            await asyncio.sleep(5)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """App lifecycle: the startup sequence runs before ``yield``, the shutdown sequence after it
    (in ``finally``). Replaces the deprecated ``@app.on_event`` handlers. Every long-lived async task
    is created via ``task_registry.spawn`` (held + cancelled on shutdown) and every daemon-thread
    service registers its stop hook via ``task_registry.register_stopper``; the ``finally`` drains both
    with one ``await task_registry.shutdown()``. Before Cycle 4 the loops + the boot reconcile ran
    fire-and-forget (no reference kept, never cancelled), leaking them across a restart."""
    from app.services import mode_enforcer
    mode_enforcer.register()

    # AE-1: reconcile any runtime-config apply revision stuck in applying/rolling_back
    # after a crash mid-apply. No-op when nothing is stuck; best-effort.
    try:
        from app.services.runtime_config_apply import recover_on_boot
        n = recover_on_boot()
        if n:
            _log.warning("recover_on_boot: reconciled %d stuck runtime-config revision(s)", n)
    except Exception as e:
        _log.warning("recover_on_boot failed: %s", e)

    # Sprint B5: load plugin-registered sensor types from /etc/robot/plugins.d/
    try:
        from app.services.sensor_registry import load_plugins, list_sensor_keys
        n = load_plugins()
        if n > 0:
            _log.info("Loaded %d sensor plugin(s). Registry keys: %s",
                      n, list_sensor_keys())
    except Exception as e:
        _log.warning("sensor plugin loading failed: %s", e)

    # Publish camera identity to Prometheus
    try:
        from app.metrics import camera_info
        camera_info.info({
            "camera_type": get_settings().camera_type,
            "hostname": socket.gethostname(),
        })
    except Exception:
        _log.debug("Prometheus camera_info not available")

    watchdogs.start_janus_watchdog()
    await watchdogs.start_snapshot_watchdog()
    task_registry.register_stopper(watchdogs.stop_all, name="watchdogs")
    # SERIAL_KEYED_BINDING_ID: fold any node-keyed remote binding_ids to
    # serial-keyed once the node's serial is known. Idempotent + fast (a local
    # store key-rename; the remote allocation is derived from each binding's
    # stored mp/port, so nothing churns). Best-effort; order-independent vs the
    # Janus reconcile below (which keys on mountpoint_id, not binding_id).
    try:
        from app.services import stream_binding_store as _sbs
        n_mig = _sbs.migrate_remote_binding_ids()
        if n_mig:
            _log.warning("startup: migrated %d remote binding_id(s) to serial-keyed", n_mig)
    except Exception as e:
        _log.warning("startup binding_id migration failed: %s", e)
    # Phase 11 (D5): reap node ops orphaned by a restart — their daemon thread died with the
    # process. Mark them `interrupted` and un-stick any node left mid-provision (provision_state
    # → failed, retriable) so it isn't stuck "provisioning" forever. Best-effort, fast (a
    # journal scan); the durable record is operations.json beside the topology store.
    try:
        from app.services import node_operation_runner
        reaped = node_operation_runner.reap_orphans()
        if reaped:
            _log.warning("startup: reaped %d orphaned node op(s): %s", len(reaped),
                         [f"{o['op_type']}:{o['node_id']}" for o in reaped])
    except Exception as e:
        _log.warning("startup op-journal reap failed: %s", e)
    # G5.3 (UNIFIED_FDIR §4.7): re-ensure every remote_producer mountpoint so
    # remote streams self-heal across a Janus/L4/gateway restart. Run it in the
    # BACKGROUND on a worker thread — a slow/unreachable Janus at boot (exactly
    # the incident this targets) must never block the event loop or delay
    # READY=1 into a systemd start-timeout restart loop. Idempotent; the remote
    # monitor is the steady-state backstop, so a few seconds' overlap before the
    # sweep finishes is harmless. Local bindings untouched.
    async def _reconcile_janus_bg() -> None:
        try:
            from app.services import binding_provision
            from app.services.sensor_lifecycle import MP_DEFAULT_SECRET
            summary = await asyncio.to_thread(
                binding_provision.reconcile_janus, mp_secret=MP_DEFAULT_SECRET)
            if summary.created or summary.failed:
                _log.warning("startup reconcile_janus: created=%d existing=%d failed=%d",
                             summary.created, summary.existing, summary.failed)
        except Exception as e:
            _log.warning("startup reconcile_janus failed: %s", e)
    task_registry.spawn(_reconcile_janus_bg(), name="reconcile_janus_boot")
    # G5: monitor remote producer bindings (isolated from the local ladder;
    # a no-op until a remote binding is provisioned).
    from app.services.remote_stream_monitor import start_remote_stream_monitor
    from app.services.remote_stream_monitor import stop as stop_remote_monitor
    start_remote_stream_monitor()
    task_registry.register_stopper(stop_remote_monitor, name="remote_stream_monitor")
    start_thermal_monitor()
    task_registry.register_stopper(stop_thermal_monitor, name="thermal")
    await janus_proxy.start_client()
    await relay_proxy.start_client()
    if get_settings().camera_type == "color_camera":
        from app.services import depth_camera_proxy
        await depth_camera_proxy.start_client()
    _sd_notify("READY=1")
    # Phase 1 P1-OBS-001: poll realsense-mux /stats every 5sec for input FPS metric export, plus the
    # systemd WATCHDOG=1 keepalive and the RSS gauge. Owned by the task registry → held + cancelled on
    # shutdown (they ran fire-and-forget, never cancelled, before — leaking across an app shutdown).
    task_registry.spawn(_watchdog_loop(), name="watchdog_keepalive")
    task_registry.spawn(_memory_gauge_loop(), name="memory_gauge")
    task_registry.spawn(_mux_fps_scraper(), name="mux_fps_scraper")

    try:
        yield
    finally:
        # One drain: the registry runs every registered stopper (signals the daemon-thread services to
        # wind down) then cancels + reaps every long-lived async task it owns — incl. the boot reconcile,
        # which was fire-and-forget before (the leak fix). Then close the HTTP clients, in order.
        await task_registry.shutdown()
        # Close HTTP proxy clients
        await janus_proxy.stop_client()
        await relay_proxy.stop_client()
        if get_settings().camera_type == "color_camera":
            from app.services import depth_camera_proxy
            await depth_camera_proxy.stop_client()
        # Close realsense_mux HTTP client
        from app.services.depth_mux_client import close as close_mux_client
        await close_mux_client()
        # Close Janus REST connection pool and thread pool
        from app.services.janus import close_client as _close_janus, _executor
        _close_janus()
        _executor.shutdown(wait=False)


def register_event_handlers(app: FastAPI) -> None:
    """Wire the app lifecycle by setting the lifespan context manager (startup before yield, shutdown
    after). Replaces the deprecated @app.on_event handlers; kept as the public seam that core/app.py
    calls + the test suite patches by name (conftest no-op-patches it to skip startup)."""
    app.router.lifespan_context = _lifespan
