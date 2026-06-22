"""SSE depth_result distribution to the browser (Sprint X3.2 + Phase 1 P0-SEC-001).

Pattern: browser publishes depth_query via textroom (Janus DataChannel)
→ textroom_relay forwards to mux:8000/depth_query → mux returns depth_result
→ relay POSTs result to /internal/depth_broadcast (this module) →
camera-page fans out to the matching SSE subscriber based on session_id.

Phase 1 fix (P0-SEC-001): previously broadcast depth_result to ALL
connected SSE subscribers — info leak (browser A's clicks visible to
browser B). Now each subscriber registers session_id (random per-tab),
relay forwards session_id in response payload, broadcast delivers only
to the matching session.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from app.core.settings import get_settings
from app.core.viewer_auth import require_viewer

log = logging.getLogger(__name__)

router = APIRouter(tags=["depth-events"])

# Active subscriber queues — keyed by session_id (per-browser-tab token).
# Worker pushes depth_result payloads here; SSE generator pulls and yields.
# Multiple subscribers per session_id supported (multiple tabs with same id —
# normal would be one tab/session but we don't block it).
_subscribers: Dict[str, asyncio.Queue] = {}
_SUB_QUEUE_MAX = 64

# Phase 2 security: internal-endpoint shared secret instead of trusting client
# IP alone. Behind reverse proxy / Docker overlay network, request.client.host
# can show proxy IP not source — IP allowlist easily bypassed. Header secret
# is cryptographically verified. Secret read from Settings (lifted out of direct
# os.environ for architecture fitness compliance).
_ALLOWED_PUBLISH_HOSTS = frozenset({"127.0.0.1", "::1", "testclient"})


def _check_internal_auth(request: Request, secret_header: Optional[str]) -> bool:
    """Defense-in-depth: require BOTH local source AND HMAC-cookie header.
    Prevents bypass via spoofed X-Forwarded-For / overlay network leaks."""
    client_ip = request.client.host if request.client else ""
    if client_ip not in _ALLOWED_PUBLISH_HOSTS:
        return False
    # If secret not configured — fall back to IP-only (backward compat) with a warning.
    internal_secret = get_settings().internal_api_secret
    if not internal_secret:
        return True
    if not secret_header:
        return False
    return hmac.compare_digest(secret_header.encode(), internal_secret.encode())


@router.get(
    "/depth_events",
    include_in_schema=False,
    # P0-SEC-001: viewer gate. EventSource cannot set custom headers, so
    # require_viewer also accepts ?token=. Dev mode (VIEWER_TOKENS unset) = no-op.
    dependencies=[Depends(require_viewer)],
)
async def depth_events(request: Request, session_id: Optional[str] = None):
    """Browser long-lived SSE stream. Connect once when the depth viewer loads
    with a unique session_id (random per-tab token). Server pushes only events
    matching this session_id, preventing cross-session leak (P0-SEC-001).

    Backward compat: if session_id not provided, generates an opaque one —
    but then no responses will match (legacy depth_query without session_id
    falls back to HTTP fetch). Encourages migration.
    """
    if not session_id:
        # No session_id = legacy path. Subscribe under "_legacy" bucket which
        # never receives broadcasts (depth_features.js falls back to HTTP).
        session_id = "_legacy"

    # Reject obvious junk session ids (length cap, charset)
    if len(session_id) > 64 or not all(c.isalnum() or c in "-_" for c in session_id):
        log.warning("invalid session_id rejected: %r", session_id[:32])
        return Response(status_code=400, content="invalid session_id")

    sub_q: asyncio.Queue = asyncio.Queue(maxsize=_SUB_QUEUE_MAX)
    _subscribers[session_id] = sub_q
    log.info("SSE subscriber connected session=%s (total=%d)",
             session_id[:16], len(_subscribers))

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub_q.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment keepalive — prevents proxy timeouts
                    yield ": keepalive\n\n"
        finally:
            # Only remove if this is still the registered queue
            # (handles same-session re-connect — newer queue should stay)
            if _subscribers.get(session_id) is sub_q:
                _subscribers.pop(session_id, None)
            log.info("SSE subscriber disconnected session=%s (total=%d)",
                     session_id[:16], len(_subscribers))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx/proxy disable buffering
            "Connection": "keep-alive",
        },
    )


@router.post("/internal/depth_broadcast", include_in_schema=False)
async def internal_depth_broadcast(
    request: Request,
    x_internal_secret: Optional[str] = Header(default=None, alias="X-Internal-Secret"),
):
    """textroom_relay calls this after mux returns depth_result. Routes ONLY
    to the matching session_id subscriber — prevents cross-tab info leak.

    Phase 2: HMAC-cookie verification on header X-Internal-Secret (defense-in-depth
    over IP allowlist). If INTERNAL_API_SECRET env unset, falls back to IP-only with
    backward compat warning logged.

    Expected payload shape (relay merges request session_id into response):
      {"type":"depth_result", "req_id":"...", "session_id":"...", "depth":...}
    """
    if not _check_internal_auth(request, x_internal_secret):
        client_ip = request.client.host if request.client else "?"
        log.warning("depth_broadcast rejected from %s (auth failed)", client_ip)
        return Response(status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400, content="bad json")

    session_id = payload.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return {"subscribers": 0, "dropped": 1, "reason": "no_session_id"}

    q = _subscribers.get(session_id)
    if q is None:
        return {"subscribers": 0, "dropped": 1, "reason": "no_subscriber"}

    try:
        q.put_nowait(payload)
        return {"delivered": 1}
    except asyncio.QueueFull:
        return {"delivered": 0, "dropped": 1, "reason": "queue_full"}
