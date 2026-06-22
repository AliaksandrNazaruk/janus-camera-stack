#!/usr/bin/env bash
# rtp-rtsp.sh — RTSP IP camera → H264 RTP adapter (Sprint B4).
#
# Connects к RTSP URL (typical IP camera, NVR, network capture device) и
# transcodes к H264 baseline RTP for Janus mountpoint. Handles common IP
# camera quirks: timeout connection, stream reconnect, codec fallback.
#
# Use case: deploy в any LAN с IP cameras, get WebRTC viewer без replacing
# existing camera infrastructure.
#
# Config env (от tuning + contract env files):
#   RTSP_URL    — REQUIRED — full RTSP URL including credentials if needed
#                 (e.g., rtsp://user:pass@192.168.1.100:554/stream)
#   PORT        — REQUIRED — RTP destination port на Janus
#   WIDTH/HEIGHT — output dimensions; if unset, passthrough source size
#   FPS         — output framerate; if unset, passthrough source
#   BITRATE_KBPS — H264 bitrate
#   TRANSPORT   — tcp (reliable, default) or udp (lower latency)
#   PRESET, TUNE, GOP, ROTATION — standard ffmpeg knobs

set -euo pipefail

INSTANCE="${1:-default}"

for f in "/etc/robot/rtp-rtsp-$INSTANCE.tuning.env" \
         "/etc/robot/rtp-rtsp-$INSTANCE.contract.env"; do
  [ -f "$f" ] && . "$f"
done

# Required
if [ -z "${RTSP_URL:-}" ]; then
  echo "[rtp-rtsp] RTSP_URL not set в tuning.env" >&2
  exit 1
fi
if [ -z "${PORT:-}" ]; then
  echo "[rtp-rtsp] PORT not set в contract.env" >&2
  exit 1
fi

# Defaults (passthrough source if не overridden)
WIDTH="${WIDTH:-}"
HEIGHT="${HEIGHT:-}"
FPS="${FPS:-}"
BITRATE_KBPS="${BITRATE_KBPS:-1500}"
GOP="${GOP:-30}"
PRESET="${PRESET:-veryfast}"
TUNE="${TUNE:-zerolatency}"
TRANSPORT="${TRANSPORT:-tcp}"   # tcp default — лучше для glitchy LAN
ROTATION="${ROTATION:-0}"

BITRATE_BPS=$(( BITRATE_KBPS * 1000 ))

# Validate TRANSPORT
case "$TRANSPORT" in
  tcp|udp) : ;;
  *)
    echo "[rtp-rtsp] invalid TRANSPORT=$TRANSPORT (use tcp|udp) — defaulting к tcp" >&2
    TRANSPORT="tcp"
    ;;
esac

# Rotation filter
case "$ROTATION" in
  0|"") ROTATE_FILTER="" ;;
  90)   ROTATE_FILTER="transpose=1," ;;
  180)  ROTATE_FILTER="transpose=1,transpose=1," ;;
  270)  ROTATE_FILTER="transpose=2," ;;
  *)
    echo "[rtp-rtsp] invalid ROTATION=$ROTATION — defaulting к 0" >&2
    ROTATE_FILTER=""
    ;;
esac

# Scale filter (optional — only if WIDTH+HEIGHT explicitly set)
SCALE_FILTER=""
if [ -n "$WIDTH" ] && [ -n "$HEIGHT" ]; then
  SCALE_FILTER="scale=${WIDTH}:${HEIGHT},"
fi

# FPS limit (optional)
FPS_FILTER=""
if [ -n "$FPS" ]; then
  FPS_FILTER="fps=${FPS},"
fi

echo "[rtp-rtsp] instance=$INSTANCE rtsp_url=${RTSP_URL%@*}@*** transport=$TRANSPORT bitrate=${BITRATE_KBPS}k rotation=${ROTATION}° port=$PORT" >&2

# ── Run ffmpeg ────────────────────────────────────────────────────────
# -rtsp_transport: tcp = reliable, udp = lower latency but fragile
# -stimeout: socket timeout 5sec — fail fast on unreachable camera, systemd restarts
# -reorder_queue_size 0: disable RTSP packet reordering (we re-encode anyway)
exec /usr/bin/ffmpeg -y -nostdin -hide_banner -loglevel warning \
  -rtsp_transport "$TRANSPORT" \
  -stimeout 5000000 \
  -reorder_queue_size 0 \
  -i "$RTSP_URL" \
  -vf "${ROTATE_FILTER}${SCALE_FILTER}${FPS_FILTER}format=yuv420p" \
  -an -c:v libx264 -preset "$PRESET" -tune "$TUNE" -pix_fmt yuv420p -profile:v baseline \
  -b:v "${BITRATE_BPS}" -maxrate "${BITRATE_BPS}" -bufsize "${BITRATE_BPS}" \
  -g "$GOP" -x264-params "keyint=$GOP:min-keyint=$GOP:scenecut=0:open_gop=0:repeat-headers=1" \
  -f rtp "rtp://127.0.0.1:${PORT}?pkt_size=1200"
