"""Prometheus metrics definitions for the camera stack.

All counters, gauges, and histograms live here so that both the
services layer and the routes layer can import them without
circular dependencies.

The ``/metrics`` HTTP endpoint is served by ``app.routes.metrics``.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ── Identity ───────────────────────────────────────────────────────
camera_info = Info(
    "camstack",
    "Camera node identity (camera_type, hostname)",
)

# ── Gauges (current state) ──────────────────────────────────────────

system_mode = Gauge(
    "camstack_system_mode",
    "Current system mode (0=NOMINAL,1=DEGRADED,2=LOCAL_ONLY,3=SAFE)",
)

recovery_ladder_level = Gauge(
    "camstack_recovery_ladder_level",
    "Current recovery ladder level (0‑4)",
)

stream_active = Gauge(
    "camstack_stream_active",
    "Whether latest watchdog check found active stream (0/1)",
)

janus_reachable = Gauge(
    "camstack_janus_reachable",
    "Whether Janus REST API is reachable (0/1)",
)

video_age_ms = Gauge(
    "camstack_video_age_ms",
    "Age in ms of latest video frame seen by watchdog (-1 if unknown)",
)

cpu_temp_celsius = Gauge(
    "camstack_cpu_temp_celsius",
    "SoC temperature in degrees Celsius (-1 if unavailable)",
)

# ── Client telemetry gauges ────────────────────────────────────────

# A3 observability: per-camera label (bounded cardinality: color/depth/ir1/ir2).
# session_id is deliberately NOT a label — it would be unbounded (cardinality
# bomb). Concurrent viewers of the SAME camera still share the gauge (gauge =
# "latest value" semantics); per-camera is the correct granularity.
client_packet_loss_ratio = Gauge(
    "camstack_client_packet_loss_ratio",
    "Latest client-reported packet loss ratio (0.0-1.0)",
    labelnames=["camera"],
)

client_frames_decoded_total = Gauge(
    "camstack_client_frames_decoded_total",
    "Latest client-reported cumulative framesDecoded",
    labelnames=["camera"],
)

client_last_report_age_seconds = Gauge(
    "camstack_client_last_report_age_seconds",
    "Seconds since last client telemetry stats_report",
    labelnames=["camera"],
)

# Phase 1 P1-OBS-002: jitter and RTT were already received but previously not stored.
# Operator alerts on degradation are now possible.
client_jitter_ms = Gauge(
    "camstack_client_jitter_ms",
    "Latest client-reported RTP jitter in milliseconds",
    labelnames=["camera"],
)

client_rtt_ms = Gauge(
    "camstack_client_rtt_ms",
    "Latest client-reported WebRTC round-trip time in milliseconds",
    labelnames=["camera"],
)

# Phase 1 P1-OBS-001: input/output FPS data-plane visibility.
mux_input_fps = Gauge(
    "camstack_mux_input_fps",
    "Latest input FPS from realsense-mux per sensor (push via /telemetry or scrape)",
    ["sensor"],
)

janus_output_fps = Gauge(
    "camstack_janus_output_fps",
    "Output FPS from Janus mountpoint (calculated as frame count delta).",
    ["mountpoint_id"],
)

# ── Counters (cumulative) ───────────────────────────────────────────

watchdog_checks_total = Counter(
    "camstack_watchdog_checks_total",
    "Total watchdog check cycles",
)

watchdog_healthy_total = Counter(
    "camstack_watchdog_healthy_total",
    "Watchdog cycles that found a healthy stream",
)

watchdog_escalations_total = Counter(
    "camstack_watchdog_escalations_total",
    "Recovery ladder escalations triggered by watchdog",
    ["level"],
)

watchdog_suppressions_total = Counter(
    "camstack_watchdog_suppressions_total",
    "Watchdog escalations suppressed because the local stream is provably alive "
    "(shared-Janus guard, UNIFIED_FDIR section 4.5) — alert if this rises sustainedly",
    ["reason"],
)

fdir_events_total = Counter(
    "camstack_fdir_events_total",
    "FDIR events emitted",
    ["domain", "severity"],
)

mode_transitions_total = Counter(
    "camstack_mode_transitions_total",
    "System mode transitions",
    ["from_mode", "to_mode"],
)

ice_connects_total = Counter(
    "camstack_ice_connects_total",
    "Client ICE connection events (reported via /telemetry)",
)

admin_auth_failures_total = Counter(
    "camstack_admin_auth_failures_total",
    "Admin authentication failures (403 responses)",
)

# P0-SEC-001: viewer auth gate failures. reason labels:
#   missing — no X-Viewer-Token header / ?token=  query
#   invalid — token present but doesn't match any configured value
#   ws_missing / ws_invalid — same for WebSocket variant
viewer_auth_failures_total = Counter(
    "camstack_viewer_auth_failures_total",
    "Viewer-gate authentication failures (401 responses)",
    ["reason"],
)

janus_summary_parse_errors_total = Counter(
    "camstack_janus_summary_parse_errors_total",
    "Janus summary parse errors (format mismatch)",
)

depth_proxy_errors_total = Counter(
    "camstack_depth_proxy_errors_total",
    "Depth camera proxy forwarding errors",
)

# ── Gauges (WebSocket) ───────────────────────────────────────────────

ws_connections_active = Gauge(
    "camstack_ws_connections_active",
    "Active WebSocket proxy connections",
)

# ── Histograms ──────────────────────────────────────────────────────

ice_connect_duration_seconds = Histogram(
    "camstack_ice_connect_duration_seconds",
    "ICE connection time as reported by client getStats()",
    buckets=[0.5, 1, 2, 3, 5, 8, 10, 15, 30],
)

ttff_seconds = Histogram(
    "camstack_ttff_seconds",
    "Time-to-first-frame as reported by client telemetry",
    buckets=[1, 2, 3, 5, 8, 10, 15, 20, 30],
)

# ── New metrics (audit remediation) ────────────────────────────────

ice_setup_failures_total = Counter(
    "camstack_ice_setup_failures_total",
    "ICE setup failures reported by client telemetry",
)

dtls_handshake_failures_total = Counter(
    "camstack_dtls_handshake_failures_total",
    "DTLS handshake failures reported by client or Janus",
)

orphaned_janus_sessions_total = Counter(
    "camstack_orphaned_janus_sessions_total",
    "Janus sessions that failed to cleanly destroy",
)

process_memory_bytes = Gauge(
    "camstack_process_memory_bytes",
    "RSS memory of this process in bytes",
)

recovery_action_duration_seconds = Histogram(
    "camstack_recovery_action_duration_seconds",
    "Time taken by FDIR recovery actions",
    ["action"],
    buckets=[1, 5, 10, 30, 60, 120],
)

# NOTE: admin_rate_limit_exceeded_total is defined in app.middleware.rate_limit
# (not here) to avoid duplicate Prometheus Counter registration.
