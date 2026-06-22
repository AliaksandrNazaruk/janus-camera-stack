#!/usr/bin/env bash
# Track A — live deploy of the ICE_POLICY/TURN_CRED_TTL relocation on THIS Pi.
#
# Run as root:  sudo infrastructure/color_node/deploy_track_a.sh
#
# Idempotent. Seeds /etc/robot/rs-runtime.env BEFORE installing the drop-in (which
# no longer carries Environment=ICE_POLICY=relay), so there is NO unset window
# (TA-C4). Restarts ONLY janus-camera-page.service — NOT a Pi reboot, NOT Janus,
# NOT the encoder. Asserts against the LIVE unit, not the repo file (TA-C1).
#
# Scope: Track A only. No /apply, rollback, FDIR, Janus restart, or encoder restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DROPIN_SRC="${SCRIPT_DIR}/systemd/janus-camera-page.service.d/override.conf"
DROPIN_DST="/etc/systemd/system/janus-camera-page.service.d/override.conf"
RUNTIME_ENV="/etc/robot/rs-runtime.env"
UNIT="janus-camera-page.service"

[ "$(id -u)" = "0" ] || { echo "ERROR: run as root (sudo $0)"; exit 1; }
[ -f "${DROPIN_SRC}" ] || { echo "ERROR: drop-in source not found: ${DROPIN_SRC}"; exit 1; }

# Anti-shadow guard (TA-C1/R-A1): the new drop-in MUST NOT carry an ICE_POLICY
# directive — it would shadow rs-runtime.env and silently no-op the relocation.
if grep -qE '^[[:space:]]*Environment=ICE_POLICY=' "${DROPIN_SRC}"; then
  echo "ERROR: ${DROPIN_SRC} still sets Environment=ICE_POLICY= — refusing (would shadow the file)"; exit 1
fi

echo "== 1. seed ${RUNTIME_ENV} (only-if-absent; TA-C7) =="
if [ ! -f "${RUNTIME_ENV}" ]; then
  install -m 0644 /dev/stdin "${RUNTIME_ENV}" <<'RSRT'
# Non-secret runtime-tunable knobs for the L4 service (writable; loaded via the
# janus-camera-page drop-in EnvironmentFile). Allowlist: ICE_POLICY, TURN_CRED_TTL only.
ICE_POLICY=relay
TURN_CRED_TTL=3600
RSRT
  echo "   seeded (ICE_POLICY=relay, TURN_CRED_TTL=3600)"
else
  echo "   exists — preserving operator-tuned values:"; sed 's/^/     /' "${RUNTIME_ENV}"
fi

echo "== 2. install the L4 drop-in (no ICE_POLICY directive) =="
install -d "$(dirname "${DROPIN_DST}")"
install -m 0644 "${DROPIN_SRC}" "${DROPIN_DST}"

echo "== 3. daemon-reload + restart ${UNIT} (L4 only) =="
systemctl daemon-reload
systemctl restart "${UNIT}"
for _ in $(seq 1 20); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/readyz 2>/dev/null)" = "200" ] && break
  sleep 0.5
done

echo "== 4. smoke (assert the LIVE unit, TA-C1) =="
fail=0
if systemctl show "${UNIT}" -p Environment --value | tr ' ' '\n' | grep -q '^ICE_POLICY='; then
  echo "   FAIL: live unit still injects ICE_POLICY via Environment="; fail=1
else
  echo "   OK: no Environment=ICE_POLICY on the live unit"
fi
if systemctl show "${UNIT}" -p EnvironmentFiles --value | grep -q "${RUNTIME_ENV}"; then
  echo "   OK: EnvironmentFile includes ${RUNTIME_ENV}"
else
  echo "   FAIL: ${RUNTIME_ENV} not in the unit's EnvironmentFiles"; fail=1
fi
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/readyz 2>/dev/null || true)
[ "${code}" = "200" ] && echo "   OK: /readyz=200" || { echo "   FAIL: /readyz=${code}"; fail=1; }
echo "   active: $(systemctl is-active "${UNIT}")"

if [ "${fail}" = "0" ]; then
  echo "Track A deploy OK — now verify effective.ice_policy/turn_cred_ttl with the admin token:"
  echo "  TOKEN=\$(grep '^CAM_ADMIN_TOKEN=' /etc/robot/camera-secrets.env | cut -d= -f2-)"
  echo "  curl -s -H \"X-Admin-Token: \$TOKEN\" http://127.0.0.1:8900/api/v1/admin/runtime-config/effective | jq '.webrtc'"
  echo "  curl -s -H \"X-Admin-Token: \$TOKEN\" http://127.0.0.1:8900/api/v1/admin/runtime-config/capabilities | jq '.blocked_impacts.NEW_SESSIONS_ONLY'"
else
  echo "Track A deploy had FAILURES — see above"; exit 1
fi
