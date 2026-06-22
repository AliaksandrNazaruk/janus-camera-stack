#!/usr/bin/env bash
# rs-stream.sh — generic FIFO-to-RTP encoder для RealSense mux consumers.
# Used by systemd template rs-stream@.service (instance: depth | ir1 | ir2 | color).
#
# Loads /etc/robot/rs-<instance>.tuning.env + /etc/robot/rs-<instance>.contract.env
# (operator-tunable + Ansible-managed; same split pattern as cam-rgb).
#
# Required env vars (set in tuning/contract files):
#   FIFO_PATH      — path к input FIFO (/run/realsense/<instance>.fifo)
#   PIX_FMT        — rawvideo pixel format (rgb24 for depth, gray for IR)
#   WIDTH/HEIGHT/FPS
#   BITRATE_KBPS, PRESET, TUNE, GOP
#   PORT           — RTP destination port на Janus
#
# Optional:
#   ROTATION       — 0|90|180|270 (ffmpeg transpose filter)

set -euo pipefail

INSTANCE="${1:-}"
if [ -z "$INSTANCE" ]; then
  echo "usage: $0 <instance>  (depth | ir1 | ir2 | color)" >&2
  exit 2
fi

# Sensor physics — PIX_FMT determined by instance, not operator-tunable.
# depth : colorized Z16 → RGB8 from realsense-mux's rs.colorizer().
# ir1/2 : Y8 grayscale from RealSense Stereo Module.
# color : RGB8 from pyrealsense2 D435i color sensor (Phase 2.1, gated by
#         RS_ENABLE_COLOR в mux). Same rgb24 layout as depth viz.
# OVERRIDING THIS from tuning.env would break decode (mismatched byte size).
case "$INSTANCE" in
  depth)
    INSTANCE_PIX_FMT="rgb24"
    INSTANCE_FIFO="/run/realsense/depth.fifo"
    ;;
  ir1|ir2)
    INSTANCE_PIX_FMT="gray"
    INSTANCE_FIFO="/run/realsense/$INSTANCE.fifo"
    ;;
  color)
    INSTANCE_PIX_FMT="rgb24"
    INSTANCE_FIFO="/run/realsense/color.fifo"
    ;;
  *)
    echo "[rs-stream] unsupported instance '$INSTANCE'" >&2
    exit 2
    ;;
esac

# Load env files (order: tuning first, contract last → contract wins).
# Note: tuning.env CAN override WIDTH/HEIGHT/FPS/BITRATE/etc but
# CANNOT override PIX_FMT (that's сensor physics, set above).
for f in "/etc/robot/rs-$INSTANCE.tuning.env" "/etc/robot/rs-$INSTANCE.contract.env"; do
  [ -f "$f" ] && . "$f"
done

# Apply hardcoded physics (override anything в env files for safety)
PIX_FMT="$INSTANCE_PIX_FMT"
FIFO_PATH="$INSTANCE_FIFO"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-15}"
BITRATE_KBPS="${BITRATE_KBPS:-1000}"
GOP="${GOP:-$FPS}"
PRESET="${PRESET:-veryfast}"
TUNE="${TUNE:-zerolatency}"
PORT="${PORT:-5006}"
# RTP destination host (G4 contract). Default loopback preserves the local
# gateway-camera path exactly; a remote producer node sets this to the gateway
# LAN IP so its encoder targets the gateway instead of its own loopback.
RTP_TARGET_HOST="${RTP_TARGET_HOST:-127.0.0.1}"
ROTATION="${ROTATION:-0}"

BITRATE_BPS=$(( BITRATE_KBPS * 1000 ))

# Rotation filter (ffmpeg transpose)
case "$ROTATION" in
  0|"")  ROTATE_FILTER="" ;;
  90)    ROTATE_FILTER="transpose=1," ;;
  180)   ROTATE_FILTER="transpose=1,transpose=1," ;;
  270)   ROTATE_FILTER="transpose=2," ;;
  *)
    echo "[rs-stream] invalid ROTATION=$ROTATION — defaulting к 0" >&2
    ROTATE_FILTER=""
    ;;
esac

# Wait for FIFO к exist (mux может ещё не успеть mkfifo при cold start)
WAIT_FIFO_SEC="${WAIT_FIFO_SEC:-30}"
deadline=$(( $(date +%s) + WAIT_FIFO_SEC ))
while [ ! -p "$FIFO_PATH" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "[rs-stream] FIFO $FIFO_PATH did not appear within ${WAIT_FIFO_SEC}s" >&2
    exit 1
  fi
  sleep 0.5
done

echo "[rs-stream] $INSTANCE: ${WIDTH}x${HEIGHT}@${FPS} pixfmt=$PIX_FMT bitrate=${BITRATE_KBPS}k rotation=${ROTATION}° target=${RTP_TARGET_HOST}:$PORT" >&2

# Optional snapshot branch (Phase 2.2) — restores rtp-rgb.sh parity для color.
# When SNAPSHOT_PATH set (rs-color.tuning.env), split decoded stream: one branch
# к RTP/H264, one к periodic MJPEG still. /api/v1/color_camera/snapshot.jpg serves it.
# depth/IR don't set SNAPSHOT_PATH → single-output path (no overhead).
SNAPSHOT_PATH="${SNAPSHOT_PATH:-}"
SNAPSHOT_FPS="${SNAPSHOT_FPS:-1}"

# H264 needs yuv420p — convert from rgb24 / gray на входе.
if [ -n "$SNAPSHOT_PATH" ]; then
  mkdir -p "$(dirname "$SNAPSHOT_PATH")" 2>/dev/null || true
  rm -f "$SNAPSHOT_PATH" 2>/dev/null || true   # non-fatal: missing/perms shouldn't kill encoder
  exec /usr/bin/ffmpeg -y -nostdin -hide_banner -loglevel warning \
    -f rawvideo -pixel_format "$PIX_FMT" -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" \
    -i "$FIFO_PATH" \
    -filter_complex "[0:v]${ROTATE_FILTER}split=2[venc][vsnap_in];[vsnap_in]fps=${SNAPSHOT_FPS},format=yuv420p[vsnap]" \
    -map "[venc]" -an -c:v libx264 -preset "$PRESET" -tune "$TUNE" -pix_fmt yuv420p -profile:v baseline \
    -b:v "${BITRATE_BPS}" -maxrate "${BITRATE_BPS}" -bufsize "${BITRATE_BPS}" \
    -g "$GOP" -x264-params keyint="$GOP":min-keyint="$GOP":scenecut=0:open_gop=0:repeat-headers=1 \
    -f rtp "rtp://${RTP_TARGET_HOST}:${PORT}?pkt_size=1200" \
    -map "[vsnap]" -an -c:v mjpeg -q:v 5 -f image2 -update 1 "$SNAPSHOT_PATH"
else
  exec /usr/bin/ffmpeg -y -nostdin -hide_banner -loglevel warning \
    -f rawvideo -pixel_format "$PIX_FMT" -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" \
    -i "$FIFO_PATH" \
    -vf "${ROTATE_FILTER}format=yuv420p" \
    -an -c:v libx264 -preset "$PRESET" -tune "$TUNE" -pix_fmt yuv420p -profile:v baseline \
    -b:v "${BITRATE_BPS}" -maxrate "${BITRATE_BPS}" -bufsize "${BITRATE_BPS}" \
    -g "$GOP" -x264-params keyint="$GOP":min-keyint="$GOP":scenecut=0:open_gop=0:repeat-headers=1 \
    -f rtp "rtp://${RTP_TARGET_HOST}:${PORT}?pkt_size=1200"
fi
