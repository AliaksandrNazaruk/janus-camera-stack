"""LAN-invariant validation helpers + config (R10) for the stream_binding_store package
(Phase 13D, D2). These service-layer network invariants (review P0-4) bind ANY caller — route,
fleet reconcile, future API — not just the HTTP route.

Leaf module (ipaddress + os only). The node/binding logic in the facade imports _is_ipv4 /
_is_loopback and reads GATEWAY_LAN_IP / CAMERA_LAN_CIDR; the facade re-exports the two constants so
the existing `sbs.GATEWAY_LAN_IP` test patch keeps reaching add_node_by_host's bare-name read. The
LAN-invariant *logic* (the gateway-IP + CIDR checks) deliberately stays inline in add_node_by_host /
_validate_remote — moving it here would defeat that monkeypatch surface. Moved verbatim."""
from __future__ import annotations

import ipaddress
import os

# Network invariants enforced at the SERVICE layer (review P0-4) so ANY caller
# (route, fleet reconcile, future API) is bound by them, not just the HTTP route.
#   GATEWAY_LAN_IP    — the gateway's own LAN IP; never a remote camera node.
#   CAMERA_LAN_CIDR   — the camera LAN; remote nodes must live inside it. Empty
#                       string disables the subnet constraint (dev/bench).
GATEWAY_LAN_IP = os.environ.get("GATEWAY_LAN_IP", "")
CAMERA_LAN_CIDR = os.environ.get("CAMERA_LAN_CIDR", "")


def _is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.IPv4Address(value).is_loopback
    except (ipaddress.AddressValueError, ValueError):
        return False
