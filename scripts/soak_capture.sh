#!/usr/bin/env bash
# soak_capture.sh — passive metrics capture for 8h+ stack soak test.
# Designed to run in background; appends to CSV every 60 sec.
#
# Usage:
#   bash scripts/soak_capture.sh [output.csv] [interval_sec]
#
# Defaults: ./soak_metrics_<YYYYMMDD_HHMM>.csv every 60 sec.
#
# What's captured:
#   ts                — Unix timestamp
#   video_age_ms      — Janus frame freshness (canary for stream health)
#   fdir_level        — Recovery ladder current level (0 = nominal)
#   client_jitter_ms  — Latest client telemetry jitter
#   client_rtt_ms     — Latest client telemetry RTT
#   l4_rss_mb         — L4 service RSS memory (leak detection)
#   janus_rss_mb      — Janus process RSS memory
#   mode              — system_mode (nominal/degraded/local_only/safe)
#   reboot_count      — circuit breaker counter
#
# Pass criteria (analyze afterwards):
#   • fdir_level stays 0 for weather run
#   • L4 RSS growth < 20% over 8h
#   • client_jitter_ms p99 < 50ms
#   • video_age_ms p99 < 200ms
#   • zero unexplained mode transitions out of nominal

set -u
OUTPUT="${1:-soak_metrics_$(date +%Y%m%d_%H%M).csv}"
INTERVAL="${2:-60}"
HEALTH_URL="${SOAK_HEALTH_URL:-http://127.0.0.1:8900/healthz}"
METRICS_URL="${SOAK_METRICS_URL:-http://127.0.0.1:8900/metrics}"
LADDER_FILE="${SOAK_LADDER_FILE:-/var/lib/camera-fdir/ladder_state.json}"
REBOOT_FILE="${SOAK_REBOOT_FILE:-/var/lib/camera-fdir/reboot_count}"

echo "ts,video_age_ms,fdir_level,client_jitter_ms,client_rtt_ms,l4_rss_mb,janus_rss_mb,mode,reboot_count" > "$OUTPUT"
echo "soak capture starting: output=$OUTPUT  interval=${INTERVAL}s  pid=$$" >&2

# Pre-cache process RSS lookup helper (avoids re-spawning ps each tick)
proc_rss_mb() {
  local pid
  pid=$(pgrep -f "$1" 2>/dev/null | head -1)
  if [ -n "$pid" ] && [ -r "/proc/$pid/status" ]; then
    awk '/VmRSS/ {print $2 / 1024}' "/proc/$pid/status" 2>/dev/null
  else
    echo ""
  fi
}

while true; do
  ts=$(date +%s)

  # Health snapshot
  health_json=$(curl -sf --max-time 3 "$HEALTH_URL" 2>/dev/null || echo "{}")
  video_age=$(echo "$health_json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("details",{}).get("video_age_ms",""))' 2>/dev/null)
  mode=$(echo "$health_json" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("mode",""))' 2>/dev/null)

  # Metrics snapshot (Prometheus)
  metrics=$(curl -sf --max-time 3 "$METRICS_URL" 2>/dev/null || echo "")
  jitter=$(echo "$metrics" | awk '/^camstack_client_jitter_ms /{print $2}' | head -1)
  rtt=$(echo "$metrics" | awk '/^camstack_client_rtt_ms /{print $2}' | head -1)

  # FDIR ladder level
  level=$(python3 -c "import json;print(json.load(open('$LADDER_FILE')).get('current_level',''))" 2>/dev/null || echo "")
  reboot_n=$(cat "$REBOOT_FILE" 2>/dev/null || echo "0")

  # Process RSS
  l4_rss=$(proc_rss_mb "main.py" | head -c 8)
  janus_rss=$(proc_rss_mb "janus -F" | head -c 8)

  printf "%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
    "$ts" "$video_age" "$level" "$jitter" "$rtt" "$l4_rss" "$janus_rss" "$mode" "$reboot_n" >> "$OUTPUT"

  sleep "$INTERVAL"
done
