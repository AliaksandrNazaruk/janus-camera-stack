#!/usr/bin/env bash
# rtp-v4l2.sh — Generic V4L2 → H264 RTP adapter (Sprint B2).
#
# Captures от any V4L2 device (USB webcam, dashcam, capture card, IP camera
# USB driver, etc.) и pushes H264 RTP к Janus mountpoint. Works в drop-in
# generic V4L2 encoder, без D435i-specific assumptions:
#   - Configurable device path (env DEVICE, default /dev/video0)
#   - Auto-detects supported pixel format if PIX_FMT не set
#   - Optional snapshot generation (off by default — saves CPU on small Pi)
#   - No D435i-specific watchdog
#
# Use case: deploy на any Linux box с USB webcam, get instant WebRTC stream.
# Combine with generic Janus + camera-page и you have full stack без RealSense.
#
# Config layered:
#   /etc/robot/rtp-v4l2-<instance>.tuning.env   — operator-tunable
#   /etc/robot/rtp-v4l2-<instance>.contract.env — Ansible-managed (PORT)

set -euo pipefail

INSTANCE="${1:-default}"

# Load env layered: tuning first, contract last (contract overrides PORT).
for f in "/etc/robot/rtp-v4l2-$INSTANCE.tuning.env" \
         "/etc/robot/rtp-v4l2-$INSTANCE.contract.env"; do
  [ -f "$f" ] && . "$f"
done

# Defaults
DEVICE="${DEVICE:-/dev/video0}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-1500}"
GOP="${GOP:-$FPS}"
PRESET="${PRESET:-veryfast}"
TUNE="${TUNE:-zerolatency}"
PORT="${PORT:-5004}"
ROTATION="${ROTATION:-0}"
SNAPSHOT_PATH="${SNAPSHOT_PATH:-}"   # empty = no snapshot
SNAPSHOT_FPS="${SNAPSHOT_FPS:-1}"

BITRATE_BPS=$(( BITRATE_KBPS * 1000 ))

# ── Pre-flight checks ─────────────────────────────────────────────────
command -v ffmpeg >/dev/null 2>&1 || { echo "[rtp-v4l2] ffmpeg not installed" >&2; exit 1; }
command -v v4l2-ctl >/dev/null 2>&1 || { echo "[rtp-v4l2] v4l2-utils not installed" >&2; exit 1; }

if [ ! -e "$DEVICE" ]; then
  echo "[rtp-v4l2] device $DEVICE not found" >&2
  exit 1
fi

# ── Pixel format auto-detect ──────────────────────────────────────────
# If PIX_FMT not set, probe device capabilities + pick best format
# preference order: YUYV (universal, low-CPU), MJPG (slightly more CPU
# к decode но widely supported), NV12, YUV420.
if [ -z "${PIX_FMT:-}" ]; then
  FORMATS=$(v4l2-ctl --device "$DEVICE" --list-formats-ext 2>/dev/null | grep -oE "'[A-Z0-9]+'" | tr -d "'" | sort -u)
  for fmt in YUYV MJPG NV12 YUV420 RGB24 BGR24; do
    if echo "$FORMATS" | grep -qx "$fmt"; then
      # Map к ffmpeg pix_fmt names
      case "$fmt" in
        YUYV)   PIX_FMT="yuyv422"; INPUT_FORMAT_OVERRIDE="" ;;
        MJPG)   PIX_FMT="mjpeg";   INPUT_FORMAT_OVERRIDE="" ;;
        NV12)   PIX_FMT="nv12";    INPUT_FORMAT_OVERRIDE="" ;;
        YUV420) PIX_FMT="yuv420p"; INPUT_FORMAT_OVERRIDE="" ;;
        RGB24)  PIX_FMT="rgb24";   INPUT_FORMAT_OVERRIDE="" ;;
        BGR24)  PIX_FMT="bgr24";   INPUT_FORMAT_OVERRIDE="" ;;
      esac
      echo "[rtp-v4l2] auto-detected PIX_FMT=$PIX_FMT for $DEVICE" >&2
      break
    fi
  done
  PIX_FMT="${PIX_FMT:-yuyv422}"   # fallback
fi

# Special handling: MJPG needs decoder (-input_format mjpeg explicit + decoder).
# Other formats are raw — direct read.
if [ "$PIX_FMT" = "mjpeg" ]; then
  V4L2_INPUT_FORMAT="mjpeg"
  INPUT_FLAGS="-f v4l2 -input_format $V4L2_INPUT_FORMAT -video_size ${WIDTH}x${HEIGHT} -framerate $FPS -i $DEVICE"
else
  INPUT_FLAGS="-f v4l2 -input_format $PIX_FMT -video_size ${WIDTH}x${HEIGHT} -framerate $FPS -i $DEVICE"
fi

# ── Rotation filter (transpose: 1=CW 90°, 2=CCW 90°) ──────────────────
case "$ROTATION" in
  0|"") ROTATE_FILTER="" ;;
  90)   ROTATE_FILTER="transpose=1," ;;
  180)  ROTATE_FILTER="transpose=1,transpose=1," ;;
  270)  ROTATE_FILTER="transpose=2," ;;
  *)
    echo "[rtp-v4l2] invalid ROTATION=$ROTATION — defaulting к 0" >&2
    ROTATE_FILTER=""
    ;;
esac

# ── Snapshot pipeline (optional) ──────────────────────────────────────
SNAPSHOT_FILTER=""
SNAPSHOT_OUTPUT=""
if [ -n "$SNAPSHOT_PATH" ]; then
  # Two output streams: H264 RTP + MJPEG snapshot
  install -d -m 0775 "$(dirname "$SNAPSHOT_PATH")"
  rm -f "$SNAPSHOT_PATH"
  SNAPSHOT_FILTER="-filter_complex [0:v]split=2[venc][vsnap_in];[vsnap_in]fps=${SNAPSHOT_FPS},${ROTATE_FILTER}format=yuv420p[vsnap];[venc]${ROTATE_FILTER}format=yuv420p[venc_out]"
  SNAPSHOT_OUTPUT="-map [vsnap] -an -c:v mjpeg -q:v 5 -f image2 -update 1 $SNAPSHOT_PATH"
  ENCODE_INPUT_MAP="-map [venc_out]"
else
  # Single output stream: H264 RTP only
  SNAPSHOT_FILTER="-vf ${ROTATE_FILTER}format=yuv420p"
  ENCODE_INPUT_MAP=""
fi

echo "[rtp-v4l2] instance=$INSTANCE device=$DEVICE format=$PIX_FMT size=${WIDTH}x${HEIGHT}@${FPS}fps bitrate=${BITRATE_KBPS}k rotation=${ROTATION}° port=$PORT snapshot=${SNAPSHOT_PATH:-disabled}" >&2

# ── Run ffmpeg ────────────────────────────────────────────────────────
exec /usr/bin/ffmpeg -y -nostdin -hide_banner -loglevel warning \
  $INPUT_FLAGS \
  $SNAPSHOT_FILTER \
  $ENCODE_INPUT_MAP -an -c:v libx264 -preset "$PRESET" -tune "$TUNE" -pix_fmt yuv420p -profile:v baseline \
  -b:v "${BITRATE_BPS}" -maxrate "${BITRATE_BPS}" -bufsize "${BITRATE_BPS}" \
  -g "$GOP" -x264-params "keyint=$GOP:min-keyint=$GOP:scenecut=0:open_gop=0:repeat-headers=1" \
  -f rtp "rtp://127.0.0.1:${PORT}?pkt_size=1200" \
  $SNAPSHOT_OUTPUT
