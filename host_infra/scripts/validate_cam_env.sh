#!/usr/bin/env bash
# host_infra/scripts/validate_cam_env.sh
#
# Validates camera env files на проде:
#   - tuning.env: values в sane ranges (operator мог накосячить руками)
#   - contract.env: matches group_vars (drift detection)
#
# Exit codes:
#   0 — all OK
#   1 — validation warnings (out-of-range, may work but suspicious)
#   2 — validation errors (won't work, MUST fix)
#   3 — drift detected между contract.env и group_vars (config out of sync)
#
# Usage:
#   ./scripts/validate_cam_env.sh                  # check all cameras
#   ./scripts/validate_cam_env.sh cam-rgb          # check one
#
# Используется в `make verify` чтобы surface drift+invalid values ДО apply.

set -uo pipefail

ETC_ROBOT="${ETC_ROBOT:-/etc/robot}"
GROUP_VARS="${GROUP_VARS:-$(dirname "$(realpath "$0")")/../group_vars/all.yml}"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }

# Parse cameras list from group_vars (YAML — minimal parser, looks for `cameras:` block keys)
parse_camera_names() {
    awk '
        /^cameras:/ { in_cameras=1; next }
        in_cameras && /^[a-z_][a-zA-Z0-9_-]*:/ { exit }
        in_cameras && /^  [a-z][a-zA-Z0-9_-]*:/ {
            name=$1; gsub(":", "", name); print name
        }
    ' "$GROUP_VARS"
}

# Extract scalar field for a camera from group_vars
get_camera_field() {
    local cam="$1" field="$2"
    awk -v cam="$cam" -v field="$field" '
        /^cameras:/ { in_cameras=1; next }
        in_cameras && /^[a-z_]/ && !/^cameras:/ { exit }
        in_cameras && $1 == cam":" { in_cam=1; next }
        in_cam && /^  [a-z]/ && $1 != cam":" { in_cam=0 }
        in_cam && $1 == field":" {
            $1=""; sub(/^ +/, ""); gsub(/^"/, ""); gsub(/"$/, "")
            print; exit
        }
    ' "$GROUP_VARS"
}

# Source env file in a subshell and echo key=value pairs
load_env() {
    local file="$1"
    [ -f "$file" ] || return 1
    (
        set -a
        # shellcheck disable=SC1090
        . "$file"
        set +a
        env | grep -E "^(PORT|PIX_FMT|WIDTH|HEIGHT|FPS|BITRATE_KBPS|GOP|PRESET|TUNE|SNAPSHOT_FPS|STALE_THRESHOLD|ROTATION)="
    )
}

errors=0
warnings=0
drift=0

check_camera() {
    local cam="$1"
    local contract="$ETC_ROBOT/$cam.contract.env"
    local tuning="$ETC_ROBOT/$cam.tuning.env"
    echo
    echo "── $cam ─────────────────────────────"

    # ── contract.env vs group_vars ─────────────────────────────────
    if [ -f "$contract" ]; then
        local expected_port actual_port
        expected_port=$(get_camera_field "$cam" "rtp_port")
        actual_port=$(load_env "$contract" 2>/dev/null | grep '^PORT=' | cut -d= -f2)
        if [ "$expected_port" = "$actual_port" ]; then
            green "  ✓ contract.env PORT=$actual_port matches group_vars"
        else
            red   "  ✗ DRIFT: contract.env PORT=$actual_port, group_vars rtp_port=$expected_port"
            drift=$((drift + 1))
        fi
    else
        yellow "  ⚠ $contract not deployed yet (run \`make apply\`)"
        warnings=$((warnings + 1))
    fi

    # ── tuning.env sane ranges ─────────────────────────────────────
    if [ -f "$tuning" ]; then
        # shellcheck disable=SC1090
        local out; out=$(load_env "$tuning" 2>/dev/null)
        # WIDTH
        local w; w=$(echo "$out" | grep '^WIDTH=' | cut -d= -f2)
        if [ -n "$w" ] && { [ "$w" -lt 160 ] || [ "$w" -gt 1920 ]; } 2>/dev/null; then
            red   "  ✗ WIDTH=$w out of range [160..1920]"
            errors=$((errors + 1))
        fi
        # HEIGHT
        local h; h=$(echo "$out" | grep '^HEIGHT=' | cut -d= -f2)
        if [ -n "$h" ] && { [ "$h" -lt 120 ] || [ "$h" -gt 1080 ]; } 2>/dev/null; then
            red   "  ✗ HEIGHT=$h out of range [120..1080]"
            errors=$((errors + 1))
        fi
        # FPS
        local fps; fps=$(echo "$out" | grep '^FPS=' | cut -d= -f2)
        if [ -n "$fps" ] && { [ "$fps" -lt 1 ] || [ "$fps" -gt 60 ]; } 2>/dev/null; then
            red   "  ✗ FPS=$fps out of range [1..60]"
            errors=$((errors + 1))
        fi
        # BITRATE_KBPS
        local br; br=$(echo "$out" | grep '^BITRATE_KBPS=' | cut -d= -f2)
        if [ -n "$br" ] && { [ "$br" -lt 200 ] || [ "$br" -gt 20000 ]; } 2>/dev/null; then
            red   "  ✗ BITRATE_KBPS=$br out of range [200..20000]"
            errors=$((errors + 1))
        fi
        # GOP должен быть <= FPS обычно (keyframe раз в секунду или чаще)
        local gop; gop=$(echo "$out" | grep '^GOP=' | cut -d= -f2)
        if [ -n "$gop" ] && [ -n "$fps" ] && [ "$gop" -gt $((fps * 4)) ] 2>/dev/null; then
            yellow "  ⚠ GOP=$gop is suspiciously high vs FPS=$fps (keyframes every $((gop / fps))s — slow recovery)"
            warnings=$((warnings + 1))
        fi
        # PRESET valid
        local preset; preset=$(echo "$out" | grep '^PRESET=' | cut -d= -f2)
        case "$preset" in
            ""|ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow) : ;;
            *) red "  ✗ PRESET=$preset not a valid x264 preset"; errors=$((errors + 1)) ;;
        esac
        # ROTATION valid (0|90|180|270 — applied via ffmpeg transpose в rtp-rgb.sh)
        local rot; rot=$(echo "$out" | grep '^ROTATION=' | cut -d= -f2)
        case "$rot" in
            ""|0|90|180|270) : ;;
            *) red "  ✗ ROTATION=$rot must be 0|90|180|270"; errors=$((errors + 1)) ;;
        esac
        # STALE_THRESHOLD vs SNAPSHOT_FPS
        local stale snap; stale=$(echo "$out" | grep '^STALE_THRESHOLD=' | cut -d= -f2)
        snap=$(echo "$out" | grep '^SNAPSHOT_FPS=' | cut -d= -f2)
        if [ -n "$stale" ] && [ -n "$snap" ] && [ "$snap" -gt 0 ] 2>/dev/null; then
            if [ "$stale" -lt $((2 / snap + 1)) ] 2>/dev/null; then
                yellow "  ⚠ STALE_THRESHOLD=$stale may trigger false watchdog kills (SNAPSHOT_FPS=$snap)"
                warnings=$((warnings + 1))
            fi
        fi
        if [ "$errors" -eq 0 ] && [ "$warnings" -eq 0 ]; then
            green "  ✓ tuning.env values в sane ranges"
        fi
    else
        yellow "  ⚠ $tuning not deployed yet"
        warnings=$((warnings + 1))
    fi

    # ── legacy file present? ───────────────────────────────────────
    if [ -f "$ETC_ROBOT/$cam.env" ]; then
        yellow "  ⚠ legacy $ETC_ROBOT/$cam.env still present (will be removed on next apply)"
        warnings=$((warnings + 1))
    fi
}

# Main
if [ $# -gt 0 ]; then
    cams="$*"
else
    cams=$(parse_camera_names)
fi

if [ -z "$cams" ]; then
    red "No cameras parsed from $GROUP_VARS"
    exit 2
fi

echo "validate_cam_env: checking ${cams// /, } against $GROUP_VARS"
for cam in $cams; do
    check_camera "$cam"
done

echo
echo "─────────────────────────────────────────"
printf "Result: %d errors, %d warnings, %d drift\n" "$errors" "$warnings" "$drift"
[ "$drift" -gt 0 ] && exit 3
[ "$errors" -gt 0 ] && exit 2
[ "$warnings" -gt 0 ] && exit 1
exit 0
