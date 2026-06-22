from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

from app.config import DEVICES, PORTS


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


def get_camera_rotation_deg() -> int:
    """Camera-mount baseline rotation in degrees CW for depth viewer CSS.

    Sysadmin sets per-deployment in /etc/robot/rs-mux.env to match physical
    camera orientation (e.g. 180 if mounted upside-down). L4 injects this
    value as --video-rotation CSS var on depth_view body; browser rotates
    displayed video. Operator's camera_config UI adds ffmpeg rotation on
    top (see rs-{sensor}.tuning.env ROTATION); JS combines both for
    click→sensor inverse.

    Re-read per call (not cached) — operator changes propagate without L4
    restart. Returns 0 if file is missing/malformed.
    """
    try:
        with open("/etc/robot/rs-mux.env") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("RS_OUTPUT_ROTATION_DEG="):
                    return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 0


# ── Env name aliases (A0 config-drift fix) ───────────────────────────────
# Deployment manifests (docker-compose, k8s) historically set different env
# names than the code reads (e.g. JANUS_API_URL vs JANUS_URL), so containers
# silently fell back to 127.0.0.1 defaults instead of the real service. Canonical
# = the name the code reads; legacy names are accepted for backward-compat and
# warned at startup. Manifests are being aligned to canonical; drop aliases after.
_ENV_ALIASES: Dict[str, List[str]] = {
    "JANUS_URL": ["JANUS_API_URL"],
    "JANUS_WS_URL_1": ["JANUS_WS_URL"],
    "RELAY_URL": ["RELAY_INTERNAL_URL"],
    "TURN_SHARED_SECRET": ["TURN_SECRET"],
    "CAM_TYPE": ["CAMERA_TYPE"],
}
_ENV_DRIFT_USED: List[str] = []  # "legacy→canonical" pairs actually present in env


def is_production() -> bool:
    """True when CAMERA_ENV selects production. Read at call time so tests and
    operators can toggle it without re-import. Default (unset) = development."""
    return os.getenv("CAMERA_ENV", "").strip().lower() in {"prod", "production"}


def _aliased_env(canonical: str, default: str | None = None) -> str | None:
    """Read ``canonical`` env var, falling back to known legacy aliases.

    Records any legacy name found so Settings.__post_init__ can warn about the
    config drift. Returns ``default`` if neither canonical nor any alias is set.
    """
    val = os.environ.get(canonical)
    if val is not None:
        return val
    for legacy in _ENV_ALIASES.get(canonical, ()):
        legacy_val = os.environ.get(legacy)
        if legacy_val is not None:
            pair = f"{legacy}→{canonical}"
            if pair not in _ENV_DRIFT_USED:
                _ENV_DRIFT_USED.append(pair)
            return legacy_val
    return default


def _int_env(key: str, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    """Fail-safe int from env. ``rs-runtime.env`` is operator/runtime-writable (Track A),
    so a malformed value must NOT raise out of the universally-called ``get_settings()``
    (that would 500 every route incl. readyz). A bad value logs + falls back to
    ``default``; out-of-range values are clamped to ``[lo, hi]`` (defense-in-depth — the
    B1 schema also bounds the runtime field)."""
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        val = int(raw.strip())
    except (ValueError, AttributeError):
        import logging as _log
        _log.getLogger(__name__).warning("Settings: %s=%r is not an int; using default %d", key, raw, default)
        return default
    if lo is not None and val < lo:
        import logging as _log
        _log.getLogger(__name__).warning("Settings: %s=%d below %d; clamping", key, val, lo)
        return lo
    if hi is not None and val > hi:
        import logging as _log
        _log.getLogger(__name__).warning("Settings: %s=%d above %d; clamping", key, val, hi)
        return hi
    return val


def _str_env(key: str, default: str, *, allowed: set[str] | None = None) -> str:
    """Fail-safe str from env. If ``allowed`` is given and the value is outside it, log and
    fall back to ``default`` (e.g. a typo'd ICE_POLICY must not silently disable relay)."""
    val = os.getenv(key, default)
    if allowed is not None and val not in allowed:
        import logging as _log
        _log.getLogger(__name__).warning("Settings: %s=%r not in %s; using %r", key, val, sorted(allowed), default)
        return default
    return val


@dataclass(frozen=True)
class Settings:
    app_title: str = "cam-control"
    app_version: str = "1.0"
    base_dir: Path = PROJECT_DIR
    templates_dir: Path = PROJECT_DIR / "templates"
    static_dir: Path = PROJECT_DIR / "static"
    # Color tuning env (operator-mutable FPS/bitrate/rotation). Phase 2 retired
    # the V4L2 path (cam-rgb.tuning.env + rtp-rgb@cam-rgb) — color now streams via
    # mux (realsense-mux → rs-stream@color), config lives in rs-color.tuning.env
    # alongside rs-depth/rs-ir{1,2}. contract.env (PORT) Ansible-owned, not L4-mutable.
    env_path: Path = Path(os.environ.get("CAM_ENV_PATH", "/etc/robot/rs-color.tuning.env"))
    lock_path: Path = Path(os.environ.get("CAM_ENV_LOCK_PATH", "/run/camera/rs-color.tuning.env.lock"))
    # camera_device = V4L2 node for /modes capability enum only (color sensor still
    # exposes a V4L2 interface even though streaming goes through pyrealsense2 mux).
    camera_device: str = os.environ.get("CAM_DEVICE", "/dev/cam-rgb")
    camera_type: str = _aliased_env("CAM_TYPE", "color_camera")
    service_name: str = os.environ.get("CAM_SERVICE", "rs-stream@color.service")
    api_key: str | None = os.environ.get("CAMCTRL_API_KEY")
    # Phase 2.2: color snapshot moved to boris-owned /run/realsense (rs-stream@color
    # runs as boris; legacy /run/cam-rgb was root-owned via masked rtp-rgb). Override
    # via SNAPSHOT_PATH env. rs-color.tuning.env SNAPSHOT_PATH must match this.
    snapshot_path: str = os.environ.get("SNAPSHOT_PATH", "/run/realsense/color-snapshot.jpg")
    # Sprint X3.4: stack default joystick mode. "off" for generic deployments,
    # "always" for robot wrappers (set via env override in systemd unit).
    stack_default_joystick_mode: str = os.environ.get("STACK_DEFAULT_JOYSTICK_MODE", "off")
    # Phase 2 security: internal endpoint HMAC shared secret. Empty = IP-only fallback.
    internal_api_secret: str = os.environ.get("INTERNAL_API_SECRET", "")
    janus_url: str = (_aliased_env("JANUS_URL") or f"http://127.0.0.1:{PORTS.JANUS_HTTP}/janus").rstrip("/")
    janus_timeout: float = float(os.environ.get("JANUS_TIMEOUT", "3"))
    janus_mount_id: int = int(os.environ.get("JANUS_MOUNT_ID", "1305"))
    janus_color_stream_id: int = int(os.environ.get("JANUS_COLOR_STREAM_ID", "1305"))
    janus_depth_stream_id: int = int(os.environ.get("JANUS_DEPTH_STREAM_ID", "1306"))
    janus_ir_stream_id: int = int(os.environ.get("JANUS_IR_STREAM_ID", "1307"))
    janus_http_base: str = os.environ.get("JANUS_HTTP", f"http://127.0.0.1:{PORTS.JANUS_HTTP}")
    relay_url: str = (_aliased_env("RELAY_URL") or f"http://127.0.0.1:{PORTS.DEPTH_PROXY}").rstrip("/")
    depth_cam_url: str = os.environ.get("DEPTH_CAM_URL", f"http://{DEVICES.DEPTH_CAMERA_IP}:{PORTS.COLOR_CAMERA}").rstrip("/")
    realsense_mux_url: str = os.environ.get("REALSENSE_MUX_URL", "http://localhost:8000")
    turn_host: str = os.getenv("TURN_HOST", DEVICES.TURN_HOST)
    turn_port: int = int(os.getenv("TURN_PORT", "3478"))
    turn_user: str = os.getenv("TURN_USER", "webrtc")
    turn_pass: str = os.getenv("TURN_PASS", "")      # MUST be set via env in production
    turn_shared_secret: str = _aliased_env("TURN_SHARED_SECRET", "")  # coturn static-auth-secret for ephemeral creds
    # TURN ephemeral credential TTL. Reduced 86400 (24h) → 3600 (1h) per
    # external review (P1-SEC-002): even ephemeral creds must have a short
    # TTL so the compromise window is bounded. 1h = balance between client
    # convenience and blast radius.
    # Track A: call-time read (default_factory) + fail-safe parse, so a future B2 apply
    # can refresh it via os.environ + get_settings.cache_clear(). Bounds 300..3600 match
    # the B1 schema R4 range (and the P1-SEC-002 1h cap). cache_clear() stays mandatory.
    turn_cred_ttl: int = field(default_factory=lambda: _int_env("TURN_CRED_TTL", 3600, lo=300, hi=3600))
    # Optional TLS port for turns:// URL. Default None → no turns:// candidate
    # added to client-config. Set when TURN server supports TLS (port 443/5349).
    turn_tls_port: int | None = (
        int(os.getenv("TURN_TLS_PORT")) if os.getenv("TURN_TLS_PORT") else None
    )
    # Track A: call-time read (default_factory) + allowlist, so a future B2 apply can
    # refresh it via os.environ + get_settings.cache_clear(). A typo must not silently
    # disable relay → falls back to "all" with a warning. cache_clear() stays mandatory.
    ice_policy: str = field(default_factory=lambda: _str_env("ICE_POLICY", "all", allowed={"all", "relay"}))

    janus_cfg_path: Path = Path(os.getenv("JANUS_CFG_PATH", "/opt/janus/etc/janus/janus.jcfg"))
    janus_nat_json: Path = Path(os.getenv("JANUS_NAT_JSON", "/etc/robot/janus-nat.json"))

    def __post_init__(self) -> None:
        if not self.turn_pass and not self.turn_shared_secret:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Neither TURN_PASS nor TURN_SHARED_SECRET is set — "
                "TURN authentication will fail for remote clients. "
                "Set at least one via environment or /etc/robot/camera-secrets.env"
            )
        if _ENV_DRIFT_USED:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Deprecated env aliases in use (config drift): %s. Rename to "
                "canonical names in deployment manifests (docker-compose/k8s).",
                ", ".join(_ENV_DRIFT_USED),
            )
    watchdog_enabled: bool = os.environ.get("CAM_WATCHDOG", "1") == "1"
    snapshot_watchdog_enabled: bool = os.environ.get("CAM_SNAPSHOT_WATCHDOG", os.environ.get("CAM_WATCHDOG", "1")) == "1"
    watchdog_interval_sec: int = int(os.environ.get("CAM_WATCHDOG_INTERVAL", "8"))
    watchdog_stale_ms: int = int(os.environ.get("CAM_WATCHDOG_STALE_MS", "10000"))
    watchdog_grace_sec: int = int(os.environ.get("WATCHDOG_GRACE_SEC", "60"))
    # SAFE-by-default: reboot recovery requires explicit opt-in via env var.
    # Default was "1" — caught by external code review (P2-REL-002). Industrial /
    # robotics stacks must NOT reboot autonomously without operator approval.
    watchdog_reboot_enabled: bool = os.environ.get("CAM_WATCHDOG_REBOOT_ENABLED", "0") == "1"
    max_fdir_reboots: int = int(os.environ.get("MAX_FDIR_REBOOTS", "2"))
    fps_profile_path: Path = Path(os.environ.get("FPS_PROFILE_PATH", "/run/camera/fps_profile"))
    mode_history_path: Path = Path(os.environ.get("MODE_HISTORY_PATH", "/run/camera/mode_history.json"))
    # NOTE: WS_MAX_CONNECTIONS / WS_MSG_RATE_PER_SEC are owned by services/ws_proxy.py (its own module
    # constants, the live readers). They were duplicated as dead Settings fields here — removed in G5 so
    # there is one owner per env var (guard #25 bans settings-owned env re-read raw in services).
    # Default: localhost + the LAN /24. To allow a PUBLIC origin (reverse-proxied
    # domain), set CORS_ORIGIN_REGEX, e.g. r"...|^https://[\w-]+\.your-domain\.example$".
    cors_origin_regex: str = os.environ.get(
        "CORS_ORIGIN_REGEX",
        r"^https?://"
        r"(localhost|127\.0\.0\.1|192\.168\.1\.(?:[1-9]?\d|1\d\d|2[0-4]\d|25[0-5]))"
        r"(:\d+)?$",
    )
    # Dict keyed by backend ID — single-entry for now, structured as dict
    # to allow future multi-backend failover (add JANUS_WS_URL_2 etc.).
    janus_ws_backends: Dict[str, str] = field(
        default_factory=lambda: {
            "1": _aliased_env("JANUS_WS_URL_1", f"ws://127.0.0.1:{PORTS.JANUS_WS}/janus-ws"),
        }
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

