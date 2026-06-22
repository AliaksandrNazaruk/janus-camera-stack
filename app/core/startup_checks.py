"""A1 — production fail-closed security checks.

In development (default) the service stays permissive: viewer auth may be off,
TURN may be unconfigured, etc. — granular warnings come from
``validate_admin_config`` / ``validate_viewer_config``.

In production (``CAMERA_ENV=production``) an insecure or clearly-misconfigured
deployment must NOT start silently. ``enforce_production_security`` aborts
startup, and ``/readyz`` reports the same issues as 503 (defense in depth).
"""
from __future__ import annotations

import ipaddress
import logging
from typing import List

from app.core.settings import Settings, is_production

logger = logging.getLogger("startup_checks")

_MIN_ADMIN_TOKEN_LEN = 16


def _is_private_or_loopback(host: str) -> bool:
    """True if ``host`` is a private/loopback/unspecified IP literal. A DNS name
    (or any non-IP string) is treated as public — only literal private IPs are
    rejected for an internet-facing TURN server."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_unspecified


def production_issues(settings: Settings) -> List[str]:
    """Return a list of production-blocking config issues (empty list = OK).

    Reads the live admin/viewer token module globals at call time so the result
    reflects the current process environment.
    """
    # Imported lazily: these modules load tokens at import time; importing here
    # (not at module top) keeps the check evaluating the current values.
    from app.core.admin import admin_token
    from app.core.viewer_auth import VIEWER_TOKENS

    issues: List[str] = []

    if not VIEWER_TOKENS:
        issues.append(
            "VIEWER_TOKENS is empty — viewer auth gate is disabled; "
            "/client-config, /depth, /janus proxy are publicly accessible"
        )

    token = admin_token()
    if token.lower() == "change-me":
        issues.append("CAM_ADMIN_TOKEN is the default placeholder 'change-me'")
    elif len(token) < _MIN_ADMIN_TOKEN_LEN:
        issues.append(
            f"CAM_ADMIN_TOKEN is too short ({len(token)} < "
            f"{_MIN_ADMIN_TOKEN_LEN} chars)"
        )

    if not settings.turn_pass and not settings.turn_shared_secret:
        issues.append(
            "no TURN credentials — neither TURN_PASS nor TURN_SHARED_SECRET set"
        )

    # TURN_HOST must be an explicit, internet-reachable address. This deployment
    # relays remote WebRTC through an external TURN server, so a private/loopback
    # TURN_HOST (e.g. an auto-derived host LAN IP) is useless for internet clients.
    turn_host = settings.turn_host.strip()
    if not turn_host:
        issues.append(
            "TURN_HOST is empty — set the public TURN server address (VPS IP or DNS)"
        )
    elif _is_private_or_loopback(turn_host):
        issues.append(
            f"TURN_HOST is private/loopback ({turn_host}) — internet clients cannot "
            "relay through it; set the public TURN VPS address or a DNS name"
        )

    # In a container/prod deployment a localhost Janus URL means the env was not
    # wired (A0 drift) — the app would talk to itself instead of the SFU.
    if any(host in settings.janus_url for host in ("127.0.0.1", "localhost")):
        issues.append(
            f"janus_url points to localhost ({settings.janus_url}) — "
            "deployment env not wired (see A0 env-name drift)"
        )

    # HOST_LAN_IP must be an explicit, non-loopback address in production —
    # loopback/empty means the network config was never set (it would otherwise
    # come from a hardcoded default that is wrong for this deployment).
    from app.config import DEVICES
    if DEVICES.HOST_LAN_IP in ("", "127.0.0.1", "0.0.0.0"):
        issues.append(
            f"HOST_LAN_IP is unconfigured ({DEVICES.HOST_LAN_IP!r}) — set HOST_LAN_IP "
            "in the deployment env to this host's real LAN address"
        )

    return issues


def enforce_production_security(settings: Settings) -> None:
    """Abort startup if running in production with insecure/broken config.

    No-op in development — dev behavior is unchanged.
    """
    if not is_production():
        return
    issues = production_issues(settings)
    if not issues:
        logger.info("Production security checks passed.")
        return
    raise RuntimeError(
        "Refusing to start: CAMERA_ENV=production but configuration is insecure:\n  - "
        + "\n  - ".join(issues)
        + "\nFix /etc/robot/camera-secrets.env (or deployment env), "
        "or set CAMERA_ENV=development for non-prod use."
    )
