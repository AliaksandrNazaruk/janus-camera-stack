"""Local fallback for shared_config.network constants.

When running inside the Aroc monorepo, ``shared_config.network`` is the
authoritative source (preferred via try/except in settings.py).
This module provides the same constants for standalone deployments:
  - CI/CD pipelines outside the monorepo
  - Unit-test isolation
  - Single-service Docker deployments

Keep these values in sync with ``shared_config/network.py``.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field


def detect_lan_ip() -> str:
    """Return this host's primary LAN IP, derived from the system (not hardcoded).

    Opens a UDP socket toward a public address and reads the local end the OS
    routing table would use — no packets are actually sent. On a multi-homed
    host this returns the *egress* interface IP; set the ``HOST_LAN_IP`` env var
    to pin a specific interface (e.g. a camera-LAN bridge). Falls back to
    loopback only if detection fails.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # no traffic; just resolves the route
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def _host_lan_ip() -> str:
    """HOST_LAN_IP precedence: explicit env > system detection > loopback."""
    return os.getenv("HOST_LAN_IP") or detect_lan_ip()


@dataclass(frozen=True)
class _ServicePorts:
    """Canonical port numbers used by janus_camera_page."""
    COLOR_CAMERA: int = 8900   # this service's listen port
    DEPTH_PROXY: int = 9000    # textroom relay / depth-proxy sidecar
    JANUS_HTTP: int = 8088     # Janus Gateway REST API
    JANUS_WS: int = 8188       # Janus Gateway WebSocket


@dataclass(frozen=True)
class _DeviceDefaults:
    """Default addresses for physical hardware.

    These are fallback values only. All addresses MUST be overridden via
    environment variables in production — no real infrastructure IPs are
    committed to source control.
    """
    DEPTH_CAMERA_IP: str = "127.0.0.1"   # override via DEPTH_CAM_URL env
    TURN_HOST: str = ""                   # must be set via TURN_HOST env
    # Derived from the system (egress IP) so it is never a hardcoded LAN address;
    # HOST_LAN_IP env overrides for multi-homed hosts. See _host_lan_ip().
    HOST_LAN_IP: str = field(default_factory=_host_lan_ip)


PORTS = _ServicePorts()
DEVICES = _DeviceDefaults()
