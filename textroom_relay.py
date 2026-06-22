"""Janus TextRoom → generic topic router (back-channel transport).

What this is:
    HTTP service that Janus invokes via textroom plugin webhook on every
    incoming datachannel message from the browser. Parses the message, routes to a sink
    based on `topic` field. Per-topic config: sink URL, rate limit, schema.

Why generic instead of joystick-only:
    Original implementation hardcoded joystick frames (axes/buttons) → POST to
    /robot/joystick. Sprint AB1 refactor: any application can use the back
    channel (voice control, telemetry, text chat, custom protocol). Joystick
    is now ONE topic, not the whole transport.

Backwards compatibility:
    Old browser code sends `{ts, axes, buttons}` without a `topic` field.
    Router detects axes/buttons → routes to topic="joystick" automatically.
    No breaking change for existing joystick app.

Configuration:
    Default config: joystick sink at http://127.0.0.1:8110/joystick/frame.
    Override via TOPIC_CONFIG_PATH env (JSON file).
    Format:
        {
          "topics": {
            "joystick": {"sink_url": "http://...", "rate_limit_hz": 60, ...},
            "voice": {"sink_url": "http://...", "rate_limit_hz": 10, ...},
            ...
          }
        }
    Reload: HUP signal or /reload endpoint (admin).

Per-topic state:
    Each topic gets its own bounded asyncio.Queue. Slow sink for one topic
    cannot block other topics (decoupled forwarding).

Ping/pong:
    System-level (not per-topic). `{type: "ping", id, ts}` measures
    browser → relay round-trip. Independent of application routing.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Response

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("textroom_relay")

# ── Configuration ─────────────────────────────────────────────────────

TOPIC_CONFIG_PATH = os.getenv("TOPIC_CONFIG_PATH", "/etc/robot/back-channel-topics.json")
QUEUE_MAX = int(os.getenv("QUEUE_MAX", "50"))
STATS_EVERY_S = float(os.getenv("STATS_EVERY_S", "2.0"))

_RS_MUX_URL = os.getenv("RS_MUX_URL", "http://127.0.0.1:8000")
# Phase 2 security: send X-Internal-Secret header on response_publish POSTs
# so receivers can verify this is the relay (not a malicious local process).
_INTERNAL_SECRET = os.getenv("INTERNAL_API_SECRET", "")

# Sprint X3.4 — generic stack defaults. Only camera-intrinsic topics here.
# Robot-application topics (joystick, mission control, etc.) MUST live in
# /etc/robot/back-channel-topics.json — see back-channel-topics.json.example.
# Stack code knows nothing about specific application semantics; it just routes
# whatever the operator declares to the configured sink_url.
_DEFAULT_TOPICS: Dict[str, Dict[str, Any]] = {
    # depth_query — camera-intrinsic (X3.2). Tied to realsense-mux: browser publishes
    # click coordinates, relay forwards to mux POST /depth_query, captures the JSON
    # response, POSTs to camera-page /internal/depth_broadcast → fans out via SSE.
    "depth_query": {
        "sink_url": _RS_MUX_URL + "/depth_query",
        "rate_limit_hz": 60,
        "validate_schema": "raw",
        "dedupe_ts": False,
        "broadcast_response": True,
        "response_publish_url": os.getenv(
            "DEPTH_PUBLISH_URL",
            "http://127.0.0.1:8900/internal/depth_broadcast",
        ),
    },
}


def _load_topic_config(path: str) -> Dict[str, Dict[str, Any]]:
    """Read topic config from JSON file. Fallback to defaults on error."""
    if not os.path.isfile(path):
        log.info("Topic config %s not found — using defaults (joystick only)", path)
        return dict(_DEFAULT_TOPICS)
    try:
        with open(path) as f:
            data = json.load(f)
        topics = data.get("topics", {})
        if not isinstance(topics, dict) or not topics:
            log.warning("Topic config %s malformed — using defaults", path)
            return dict(_DEFAULT_TOPICS)
        # Merge with defaults — defaults provide any missing required fields
        merged = dict(_DEFAULT_TOPICS)
        for name, cfg in topics.items():
            merged[name] = {**_DEFAULT_TOPICS.get(name, {}), **cfg}
        log.info("Topic config loaded: %d topics from %s", len(merged), path)
        return merged
    except Exception as exc:
        log.error("Failed to parse %s: %s — using defaults", path, exc)
        return dict(_DEFAULT_TOPICS)


# ── State ─────────────────────────────────────────────────────────────

app = FastAPI()

_topics: Dict[str, Dict[str, Any]] = {}
_queues: Dict[str, asyncio.Queue] = {}
_workers: Dict[str, asyncio.Task] = {}
_client: Optional[httpx.AsyncClient] = None

# Per-topic counters
_stats: Dict[str, Dict[str, int]] = {}
_last_stats_t = 0.0

# System-level ping/pong (transport health, not per-topic)
_last_pong: Optional[Dict[str, Any]] = None



def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_stats() -> Dict[str, int]:
    return {"seen": 0, "enqueued": 0, "dropped": 0, "forwarded": 0,
            "errors": 0, "last_ts_forwarded": 0}


# ── Message parsing + topic detection ─────────────────────────────────

def _is_ping(payload: Dict[str, Any]) -> bool:
    return isinstance(payload, dict) and payload.get("type") == "ping" and "ts" in payload


def _detect_topic(payload: Dict[str, Any]) -> str:
    """Determine topic from payload — explicit `topic` field only (X3.4).

    Pre-X3.4 had axes+buttons → "joystick" autodetect. Removed: stack must NOT
    know about specific applications. All clients MUST send an explicit `topic` field.
    Backward compat for old browsers: they must upgrade to the BackChannel SDK.
    """
    if not isinstance(payload, dict):
        return "unknown"
    explicit = payload.get("topic")
    if isinstance(explicit, str) and explicit:
        return explicit
    return "unknown"


def _validate(payload: Dict[str, Any], schema: str) -> bool:
    """Apply per-topic validation. X3.4: only generic schemas — robot-application
    schemas (axes/buttons bounds checking, etc) must be done by the sink itself.

    Schemas:
      - "raw":  must be a dict (any keys allowed)
      - "none": no validation (any JSON allowed)
    """
    if schema == "none":
        return True
    if schema == "raw":
        return isinstance(payload, dict)
    log.warning("Unknown schema %r — defaulting to reject", schema)
    return False


def _extract_inner_payload(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Janus TextRoom hook sends body['text'] as a JSON-encoded string."""
    if body.get("textroom") != "message":
        return None
    raw = body.get("text")
    if not raw:
        return None
    try:
        inner = json.loads(raw)
        if not isinstance(inner, dict):
            return None
        ts = inner.get("ts")
        if isinstance(ts, float):
            inner["ts"] = int(ts)
        return inner
    except Exception:
        return None


# ── Per-topic forwarding worker ───────────────────────────────────────

async def _forward_worker(topic_name: str) -> None:
    """Consume the queue for one topic, POST to sink_url. Optionally broadcast
    the sink's response to SSE subscribers (depth_query pattern)."""
    global _client
    assert _client is not None

    cfg = _topics[topic_name]
    sink_url = cfg["sink_url"]
    dedupe_ts = cfg.get("dedupe_ts", False)
    broadcast_response = cfg.get("broadcast_response", False)
    response_publish_url = cfg.get("response_publish_url")
    # Phase 2: enforce rate_limit_hz — token bucket per topic. Drops frames
    # over budget rather than queueing them (matches fire-and-forget semantics).
    rate_limit_hz = float(cfg.get("rate_limit_hz", 0) or 0)
    min_interval_s = (1.0 / rate_limit_hz) if rate_limit_hz > 0 else 0.0
    last_forward_t = 0.0
    queue = _queues[topic_name]
    stats = _stats[topic_name]

    log.info("Worker started for topic %r → %s (broadcast_response=%s)",
             topic_name, sink_url, broadcast_response)

    while True:
        payload = await queue.get()
        try:
            # Phase 2: enforce rate limit. If frames coming faster than rate_limit_hz,
            # drop silently. Protects sinks from a malicious flood.
            if min_interval_s > 0:
                now = time.time()
                if now - last_forward_t < min_interval_s:
                    stats["dropped"] += 1
                    queue.task_done()
                    continue
            if dedupe_ts:
                ts = int(payload.get("ts", 0))
                if ts <= stats["last_ts_forwarded"]:
                    queue.task_done()
                    continue

            resp = await _client.post(sink_url, json=payload)
            if resp.status_code == 200:
                if dedupe_ts:
                    stats["last_ts_forwarded"] = int(payload.get("ts", 0))
                stats["forwarded"] += 1
                last_forward_t = time.time()
                # Topic with broadcast_response: forward the sink's response to
                # the SSE publish URL (camera-page hosts the actual SSE stream to browsers).
                # P0-SEC-001 (Phase 1): merge session_id from request into response
                # so camera-page can route only to the matching subscriber. If the client
                # does not send session_id (legacy), the response goes to the "_legacy" bucket
                # which never has subscribers — silent drop.
                if broadcast_response and response_publish_url:
                    try:
                        reply = resp.json()
                        # Always copy session_id (if present) from request to response.
                        # Sink (mux) ignores session_id; relay re-attaches here.
                        sid = payload.get("session_id")
                        if isinstance(sid, str):
                            reply["session_id"] = sid
                        # Phase 2: send X-Internal-Secret if configured
                        publish_headers = {}
                        if _INTERNAL_SECRET:
                            publish_headers["X-Internal-Secret"] = _INTERNAL_SECRET
                        pub_resp = await _client.post(
                            response_publish_url, json=reply, headers=publish_headers,
                        )
                        if pub_resp.status_code != 200:
                            log.warning("[%s] response_publish HTTP %s",
                                        topic_name, pub_resp.status_code)
                    except Exception as e:
                        log.warning("[%s] response_publish error: %s", topic_name, e)
            else:
                stats["errors"] += 1
                log.warning("[%s] sink HTTP %s: %s",
                            topic_name, resp.status_code, resp.text[:200])

        except Exception as exc:
            stats["errors"] += 1
            log.warning("[%s] forward error: %s", topic_name, exc)

        finally:
            queue.task_done()


def _maybe_log_stats() -> None:
    global _last_stats_t
    now = time.time()
    if now - _last_stats_t < STATS_EVERY_S:
        return
    _last_stats_t = now
    summary = " | ".join(
        f"{name}: seen={s['seen']} fwd={s['forwarded']} drop={s['dropped']} err={s['errors']} q={_queues[name].qsize()}"
        for name, s in _stats.items()
    )
    log.info("stats: %s", summary)


# ── Lifecycle ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    global _client, _topics, _queues, _workers, _stats

    _topics = _load_topic_config(TOPIC_CONFIG_PATH)

    timeout = httpx.Timeout(connect=0.2, read=0.6, write=0.6, pool=0.6)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    _client = httpx.AsyncClient(timeout=timeout, limits=limits)

    for topic_name in _topics:
        _queues[topic_name] = asyncio.Queue(maxsize=QUEUE_MAX)
        _stats[topic_name] = _new_stats()
        _workers[topic_name] = asyncio.create_task(_forward_worker(topic_name))

    log.info("Relay started. Topics: %s. QUEUE_MAX=%d",
             list(_topics.keys()), QUEUE_MAX)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _client
    for task in _workers.values():
        task.cancel()
    if _client:
        await _client.aclose()


# ── HTTP endpoints ────────────────────────────────────────────────────

# Allowed source IPs (Janus runs locally → only localhost accepted).
# `testclient` permitted for unit tests (FastAPI TestClient default hostname).
_ALLOWED_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "testclient"})


@app.post("/textroom-hook")
async def textroom_hook(request: Request) -> Response:
    """Receive datachannel message from Janus, route to per-topic queue."""
    global _last_pong

    client_ip = request.client.host if request.client else ""
    if client_ip not in _ALLOWED_CLIENT_HOSTS:
        log.warning("textroom-hook rejected from non-local IP: %s", client_ip)
        return Response(status_code=403, content="forbidden")

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="bad json")

    inner = _extract_inner_payload(body)
    if inner is None:
        return Response(status_code=200, content="ok")

    inner["relay_rx_ms"] = _now_ms()

    # Ping path — system-level, not topic-routed
    if _is_ping(inner):
        _last_pong = {
            "id": inner.get("id", 0),
            "browser_ts": int(inner.get("ts", 0)),
            "relay_rx_ms": inner["relay_rx_ms"],
            "relay_fwd_ms": _now_ms(),
            "robot_elapsed_ms": 0,
            "server_ms": _now_ms(),
        }
        return Response(status_code=200, content="ok")

    # Topic-routed path
    topic = _detect_topic(inner)
    if topic not in _topics:
        # Unknown topic — silent drop (might be legacy or out-of-spec)
        return Response(status_code=200, content="ok")

    cfg = _topics[topic]
    stats = _stats[topic]
    stats["seen"] += 1

    if not _validate(inner, cfg.get("validate_schema", "raw")):
        stats["dropped"] += 1
        return Response(status_code=200, content="ok")

    queue = _queues[topic]
    # Coalescing — on overflow drop oldest, accept newest (avoid latency buildup)
    if queue.full():
        try:
            queue.get_nowait()
            queue.task_done()
        except Exception:
            pass
        stats["dropped"] += 1

    try:
        queue.put_nowait(inner)
        stats["enqueued"] += 1
    except Exception:
        stats["dropped"] += 1

    _maybe_log_stats()
    return Response(status_code=200, content="ok")


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "topics": {
            name: {
                "sink_url": cfg["sink_url"],
                "queue": {"size": _queues[name].qsize(), "max": QUEUE_MAX},
                "stats": _stats[name],
            }
            for name, cfg in _topics.items()
        },
        "now_ms": _now_ms(),
    }


@app.get("/time")
async def server_time() -> Dict[str, int]:
    """Server time for clock-sync with the browser."""
    return {"server_ms": _now_ms()}


@app.get("/pong")
async def pong() -> Dict[str, Any]:
    """Last ping → relay → browser round-trip measurement.

    Universal endpoint (not tied to joystick) — measures transport health.
    """
    if _last_pong is None:
        return {"id": -1}
    return _last_pong


@app.get("/topics")
async def topics() -> Dict[str, Any]:
    """List configured topics + their sink URLs (for debug + UI)."""
    return {
        "topics": list(_topics.keys()),
        "config_path": TOPIC_CONFIG_PATH,
        "details": {
            name: {
                "sink_url": cfg["sink_url"],
                "schema": cfg.get("validate_schema", "raw"),
                "rate_limit_hz": cfg.get("rate_limit_hz"),
                "dedupe_ts": cfg.get("dedupe_ts", False),
            }
            for name, cfg in _topics.items()
        },
    }
