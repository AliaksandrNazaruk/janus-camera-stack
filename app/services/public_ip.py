"""Detect public IP — for nat_1_1_mapping auto-fill in admin_config UI.

Tries in order:
  1. STUN reflexive address from stun.l.google.com (most reliable)
  2. HTTP fallback: ifconfig.me / icanhazip.com
  3. Fail (operator types it manually)

Returns IP + method used. Caller decides whether to persist.
"""
from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("public_ip")


@dataclass(frozen=True)
class PublicIpResult:
    ip: Optional[str]
    method: str    # "stun" | "http-ifconfig.me" | "http-icanhazip" | "failed"
    error: Optional[str] = None


def _stun_query(host: str = "stun.l.google.com", port: int = 19302, timeout: float = 3.0) -> Optional[str]:
    """Minimal STUN binding request — parse XOR-MAPPED-ADDRESS."""
    # STUN message: type=0x0001 (binding request), length=0, magic cookie, txid
    msg_type = 0x0001
    msg_length = 0
    magic_cookie = 0x2112A442
    txid = b"\x00" * 12  # 12 random bytes (zeros OK for one-shot)
    req = struct.pack("!HHI12s", msg_type, msg_length, magic_cookie, txid)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(req, (host, port))
        data, _ = sock.recvfrom(2048)
    except (socket.timeout, OSError) as exc:
        log.debug("STUN query failed: %s", exc)
        return None
    finally:
        sock.close()
    # Parse response
    if len(data) < 20:
        return None
    resp_type, resp_len, _cookie, _txid = struct.unpack("!HHI12s", data[:20])
    if resp_type != 0x0101:  # binding success
        return None
    # Walk TLV attributes
    pos = 20
    end = 20 + resp_len
    while pos + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[pos:pos + 4])
        pos += 4
        if attr_type == 0x0020:  # XOR-MAPPED-ADDRESS
            if attr_len < 8:
                return None
            _, family, xport = struct.unpack("!BBH", data[pos:pos + 4])
            if family != 0x01:  # IPv4 only
                return None
            xip = data[pos + 4:pos + 8]
            # De-XOR
            port_xor = xport ^ (magic_cookie >> 16)  # noqa: F841
            ip_xor = struct.unpack("!I", xip)[0] ^ magic_cookie
            ip_str = socket.inet_ntoa(struct.pack("!I", ip_xor))
            return ip_str
        # Align to 4 bytes
        pos += attr_len + (-attr_len % 4)
    return None


def _http_probe(url: str, timeout: float = 3.0) -> Optional[str]:
    try:
        r = httpx.get(url, timeout=timeout, headers={"User-Agent": "camera-page-admin/1"})
        if r.status_code == 200:
            ip = r.text.strip()
            # Sanity check it looks like an IPv4
            parts = ip.split(".")
            if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return ip
    except (httpx.RequestError, ValueError) as exc:
        log.debug("HTTP probe %s failed: %s", url, exc)
    return None


def detect() -> PublicIpResult:
    """Detect public IP. Tries STUN first, falls back to HTTP probes."""
    # STUN (preferred — UDP-based, matches what Janus will see)
    ip = _stun_query()
    if ip:
        return PublicIpResult(ip=ip, method="stun")

    for url, label in [
        ("https://ifconfig.me", "http-ifconfig.me"),
        ("https://icanhazip.com", "http-icanhazip"),
    ]:
        ip = _http_probe(url)
        if ip:
            return PublicIpResult(ip=ip, method=label)

    return PublicIpResult(
        ip=None,
        method="failed",
        error="STUN + HTTP probes failed — set nat_1_1_mapping manually",
    )
