"""coturn TURN REST-API ephemeral credential generation (Cycle 10 — extracted from nat_config).

A pure, self-contained helper: HMAC-SHA1 over a time-expiry username, the coturn ``use-auth-secret`` /
``static-auth-secret`` scheme. No coupling to the NAT config store — kept separate so the TURN-auth
concern is one focused module (consumers: routes/janus.py, routes/system.py)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Tuple


def generate_turn_credentials(
    shared_secret: str,
    user: str = "webrtc",
    ttl: int = 86400,
) -> Tuple[str, str]:
    """Generate coturn TURN REST API ephemeral credentials.

    Uses the same algorithm as coturn ``use-auth-secret`` /
    ``static-auth-secret``:
      username = "<unix-expiry>:<user>"
      credential = Base64(HMAC-SHA1(username, shared_secret))

    Returns (username, credential).
    """
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{user}"
    mac = hmac.new(shared_secret.encode(), username.encode(), hashlib.sha1)
    credential = base64.b64encode(mac.digest()).decode()
    return username, credential
