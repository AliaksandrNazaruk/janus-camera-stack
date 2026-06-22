#!/usr/bin/env bash
# host_infra/roles/janus/files/janus-nat-updater.sh
#
# Auto-update nat_1_1_mapping in janus.jcfg when public IP changes.
# Runs via cron (*/15 min), но coordinates с janus-turn-rotator (daily timer)
# через flock — оба writer'а на одном файле janus.jcfg.
#
# Why flock:
#   Раньше: оба скрипта делали read-modify-write на janus.jcfg одновременно
#   → race condition (один писал свои изменения, второй их затирал).
#   Теперь: оба acquire /var/lock/janus-jcfg.lock перед любой mutation.
#
# Why atomic write (tmp + rename) вместо sed -i:
#   sed -i создаёт temp в той же директории и rename — но если процесс убит
#   посреди — может остаться broken jcfg (хотя sed достаточно надёжен).
#   Atomic write более явный — backup перед, single rename операция.
#
# Exit codes:
#   0 — no action (IP same) OR updated successfully
#   1 — could not determine public IP
#   2 — flock timeout (другой writer ещё работает > 60s)
#   3 — jcfg mutation error
set -euo pipefail

JANUS_CFG="${JANUS_CFG:-/opt/janus/etc/janus/janus.jcfg}"
IP_CACHE="${IP_CACHE:-/var/tmp/janus-public-ip.cache}"
LOCK_FILE="${LOCK_FILE:-/var/lock/janus-jcfg.lock}"
LOCK_TIMEOUT="${LOCK_TIMEOUT:-60}"   # seconds
BACKUP_DIR="${BACKUP_DIR:-/var/backups/janus-nat-updater}"
LOG_TAG="nat-mapping"
RESTART_JANUS="${RESTART_JANUS:-1}"

log() { logger -t "$LOG_TAG" "$@"; printf '%s [%s] %s\n' "$(date '+%FT%T')" "$LOG_TAG" "$*" >&2; }

# ── IP detection (no file mutation — outside lock) ────────────────────
# FORCE_NEW_IP env override allows tests + ops emergency to bypass network probe.
NEW_IP="${FORCE_NEW_IP:-}"
if [[ -z "$NEW_IP" ]]; then
    NEW_IP=$(curl -sf --max-time 5 https://ifconfig.me 2>/dev/null \
          || curl -sf --max-time 5 https://api.ipify.org 2>/dev/null \
          || curl -sf --max-time 5 https://checkip.amazonaws.com 2>/dev/null \
          || echo "")
fi

if [[ -z "$NEW_IP" ]]; then
    log "WARN: could not determine public IP"
    exit 1
fi

# Validate IP format (basic — IPv4 only)
if ! [[ "$NEW_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log "WARN: bogus IP from detector: $NEW_IP"
    exit 1
fi

OLD_IP=""
[[ -f "$IP_CACHE" ]] && OLD_IP=$(cat "$IP_CACHE")

if [[ "$NEW_IP" == "$OLD_IP" ]]; then
    exit 0   # no change — no need to acquire lock
fi

# ── Atomic mutation under flock (coordinated с janus-turn-rotator) ────
mkdir -p "$BACKUP_DIR"
(
    if ! flock -w "$LOCK_TIMEOUT" 200; then
        log "ERROR: could not acquire $LOCK_FILE within ${LOCK_TIMEOUT}s"
        exit 2
    fi

    # Backup
    cp -p "$JANUS_CFG" "$BACKUP_DIR/$(basename "$JANUS_CFG").$(date +%Y%m%d_%H%M%S).bak"

    # Patch into tmp file
    TMP="${JANUS_CFG}.tmp.$$"
    if grep -q "nat_1_1_mapping" "$JANUS_CFG"; then
        sed "s|nat_1_1_mapping = \".*\"|nat_1_1_mapping = \"$NEW_IP\"|" "$JANUS_CFG" > "$TMP"
    else
        sed "/^nat: {/a \\  nat_1_1_mapping = \"$NEW_IP\"" "$JANUS_CFG" > "$TMP"
    fi

    # Sanity check — must contain new IP
    if ! grep -q "nat_1_1_mapping = \"$NEW_IP\"" "$TMP"; then
        rm -f "$TMP"
        log "ERROR: post-patch verification failed (sed didn't apply)"
        exit 3
    fi

    # Atomic swap
    chown --reference="$JANUS_CFG" "$TMP"
    chmod --reference="$JANUS_CFG" "$TMP"
    mv "$TMP" "$JANUS_CFG"

    # Cache new IP only after success
    echo "$NEW_IP" > "$IP_CACHE"
    log "Updated nat_1_1_mapping: ${OLD_IP:-none} -> $NEW_IP"

    if [[ "$RESTART_JANUS" == "1" ]]; then
        if systemctl restart janus.service; then
            log "janus restarted"
        else
            log "ERROR: janus restart failed — old creds still in memory until next restart"
            exit 3
        fi
    fi
) 200>"$LOCK_FILE"
