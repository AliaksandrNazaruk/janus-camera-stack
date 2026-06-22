#!/usr/bin/env bash
# camera-node bootstrap — STANDALONE, NODE-ONLY, DEFAULT-DENY, sensor-AGNOSTIC.
#
# Operates the node "pipe": RealSense mux + per-sensor rs-stream encoders, sending
# RTP to the gateway's Janus. NO stream is special — the operator activates any
# subset of {color,depth,ir1,ir2}. Default-deny BY CONSTRUCTION: no Janus, no
# coturn, no TURN/Cloudflare, no secret generation (review S7). Idempotent.
#
# Modes:
#   probe                         enumerate RealSense cameras (JSON); deploy nothing
#   deploy                        install + start the mux pipe (no streams activated)
#   activate --sensor S \
#       --rtp-target-host H --rtp-port P     activate one stream S -> RTP to H:P
#   deactivate --sensor S         stop one stream S
#
# Usage:  sudo ./bootstrap.sh deploy
#         sudo ./bootstrap.sh activate --sensor depth --rtp-target-host 192.168.1.10 --rtp-port 5102
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
SENSOR=""
RTP_TARGET_HOST=""
RTP_PORT=""
AGENT_TOKEN=""
BIN_DIR=/usr/local/bin
UNIT_DIR=/etc/systemd/system
ENV_DIR=/etc/robot
WHEELS_DIR="${BUNDLE_DIR}/wheels"
FILES_DIR="${BUNDLE_DIR}/files"
PROBE_CLI="${BUNDLE_DIR}/probe/realsense_probe_cli.py"

log() { printf '[node-bootstrap] %s\n' "$*" >&2; }
die() { printf '[node-bootstrap][err] %s\n' "$*" >&2; exit 1; }

# run ARGV directly — NO eval (review H2). dry-run prints a shell-quoted preview.
# Use for everything that does not need shell features.
run() {
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run]'; printf ' %q' "$@"; printf '\n' >&2
  else "$@"; fi
}
# sh_c: run a TRUSTED, CONSTANT shell snippet (needs pipe / && / redirect / glob).
# SECURITY: never pass untrusted input here (rtp host/port/token) — those are
# validated + go through argv (run / write_file). Only bundle-internal constants
# and already-validated $SENSOR reach this.
sh_c() {
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] sh -c: %s\n' "$1" >&2; else bash -c "$1"; fi
}
# write_file MODE PATH CONTENT — argv-only write (no eval, no interpolation into a
# shell string). umask 077 so a secret env is never world-readable mid-write.
write_file() {
  local mode="$1" path="$2" content="$3"
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] write %s (mode %s)\n' "$path" "$mode" >&2; return 0; fi
  ( umask 077; printf '%s\n' "$content" > "$path" )
  chmod "$mode" "$path"
}
usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

MODE="${1:-}"; shift 2>/dev/null || true
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)            DRY_RUN=1 ;;
    --sensor)             SENSOR="${2:-}"; shift ;;
    --sensor=*)           SENSOR="${1#*=}" ;;
    --rtp-target-host)    RTP_TARGET_HOST="${2:-}"; shift ;;
    --rtp-target-host=*)  RTP_TARGET_HOST="${1#*=}" ;;
    --rtp-port)           RTP_PORT="${2:-}"; shift ;;
    --rtp-port=*)         RTP_PORT="${1#*=}" ;;
    --agent-token)        AGENT_TOKEN="${2:-}"; shift ;;
    --agent-token=*)      AGENT_TOKEN="${1#*=}" ;;
    --help|-h)            usage; exit 0 ;;
    *)                    die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

_need_root() { [ "$(id -u)" = 0 ] || [ "$DRY_RUN" = 1 ] || die "must run as root (systemd/install); re-run with sudo"; }
_valid_sensor() { case "$1" in color|depth|ir1|ir2) ;; *) die "invalid --sensor '$1' (color|depth|ir1|ir2)";; esac; }
# Validate untrusted args so they can never carry shell metacharacters (review H1/H2).
_valid_port() {
  case "$1" in ''|*[!0-9]*) die "invalid --rtp-port '$1' (numeric)";; esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || die "--rtp-port out of range: $1"
}
_valid_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] \
    || die "invalid --rtp-target-host '$1' (IPv4)"
  local o
  for o in "${BASH_REMATCH[@]:1}"; do [ "$o" -le 255 ] || die "invalid --rtp-target-host '$1' (octet>255)"; done
}
_valid_token() { case "$1" in ''|*[!A-Za-z0-9_-]*) die "invalid --agent-token (charset A-Za-z0-9_-)";; esac; }

verify_manifest() {
  [ -f "${BUNDLE_DIR}/SHA256SUMS" ] || { log "no SHA256SUMS — skipping integrity check (dev)"; return 0; }
  log "verifying bundle integrity (SHA256SUMS)"
  ( cd "$BUNDLE_DIR" && sha256sum -c --quiet SHA256SUMS ) || die "bundle integrity check FAILED"
}

install_runtime_deps() {
  if command -v ffmpeg >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q 'libusb-1.0'; then
    log "ffmpeg + libusb already present — skipping runtime dep install (idempotent / offline)"; return 0
  fi
  if ls "${BUNDLE_DIR}/deb/"*.deb >/dev/null 2>&1; then
    sh_c "dpkg -i '${BUNDLE_DIR}/deb/'*.deb || apt-get -f install -y"
  else
    log "no bundled .deb — apt fallback (needs network; offline-harden in P4)"
    sh_c "apt-get update && apt-get install -y ffmpeg libusb-1.0-0"
  fi
}

install_pyrealsense() {
  if python3 -c "import pyrealsense2" >/dev/null 2>&1; then
    log "pyrealsense2 already present — skipping install (idempotent / offline; e.g. built on the node)"; return 0
  fi
  if ls "${WHEELS_DIR}/"pyrealsense2-*.whl >/dev/null 2>&1; then
    run pip3 install --no-index --find-links "${WHEELS_DIR}" pyrealsense2 numpy
  else
    log "no bundled wheel — pip fallback (needs network; offline-harden in P4)"
    run pip3 install pyrealsense2 numpy
  fi
}

install_stack() {
  local mux_py; mux_py="$(command -v python3 || echo /usr/bin/python3)"
  log "installing mux + encoder units (mux python=${mux_py})"
  run install -m 0755 "${FILES_DIR}/rs-stream.sh" "${BIN_DIR}/rs-stream.sh"
  run install -m 0755 "${FILES_DIR}/realsense-mux.py" "${BIN_DIR}/realsense-mux.py"
  run install -m 0644 "${FILES_DIR}/rs-stream@.service" "${UNIT_DIR}/rs-stream@.service"
  # realsense-mux.service ships the GATEWAY venv python; rewrite to the node's.
  # Constants only ($mux_py is a local `command -v python3` path) -> sh_c is safe.
  sh_c "sed 's#^ExecStart=[^ ]* #ExecStart=${mux_py} #' '${FILES_DIR}/realsense-mux.service' > '${UNIT_DIR}/realsense-mux.service'"
  run chmod 0644 "${UNIT_DIR}/realsense-mux.service"
  # node-agent control plane + a persistent probe it can call
  run install -m 0755 "${PROBE_CLI}" "${BIN_DIR}/realsense_probe_cli.py"
  run install -m 0755 "${BUNDLE_DIR}/node-agent/camera-node-agent.py" "${BIN_DIR}/camera-node-agent.py"
  run install -m 0644 "${BUNDLE_DIR}/node-agent/camera-node-agent.service" "${UNIT_DIR}/camera-node-agent.service"
  run systemctl daemon-reload
}

install_sysctl() {
  # realsense-mux enlarges each FIFO to >= a full frame via F_SETPIPE_SZ; the
  # default fs.pipe-max-size (1MB) is too small for a 900KB RGB frame -> EPERM ->
  # every frame dropped -> encoder starves. Ship the same conf the gateway uses.
  if [ -f "${FILES_DIR}/sysctl-realsense-mux.conf" ]; then
    run install -m 0644 "${FILES_DIR}/sysctl-realsense-mux.conf" /etc/sysctl.d/99-realsense-mux.conf
    sh_c "sysctl -p /etc/sysctl.d/99-realsense-mux.conf >/dev/null"
    log "applied fs.pipe-max-size (sysctl-realsense-mux.conf)"
  fi
}

_set_mux_env() {  # idempotent KEY=VALUE in rs-mux.env (k/v are internal constants)
  local k="$1" v="$2" f="${ENV_DIR}/rs-mux.env"
  run touch "$f"
  sh_c "grep -q '^${k}=' '$f' && sed -i 's#^${k}=.*#${k}=${v}#' '$f' || echo '${k}=${v}' >> '$f'"
}

_wait_fifo() {  # wait until the mux is producing a FIFO (reader connects aligned)
  local p="$1" i
  [ "$DRY_RUN" = 1 ] && { printf '[dry-run] wait for FIFO %s\n' "$p" >&2; return 0; }
  for i in $(seq 1 20); do [ -p "$p" ] && { sleep 1; return 0; }; sleep 0.5; done
  log "warn: FIFO $p did not appear in 10s; encoder will retry"
}

# ── modes ─────────────────────────────────────────────────────────────
cmd_probe() {
  python3 -c "import pyrealsense2" >/dev/null 2>&1 || die "pyrealsense2 not importable; run 'deploy' to install deps"
  run python3 "${PROBE_CLI}" --json
  log "probe: nothing installed or deployed, host left clean"
}

cmd_deploy() {  # deploy the pipe: mux + units, no streams (sensor-agnostic)
  _need_root
  verify_manifest
  install_runtime_deps
  install_pyrealsense
  sh_c "python3 '${PROBE_CLI}' --require >/dev/null"   # refuse to deploy onto an empty host
  install_stack
  install_sysctl                                       # large pipe buffers BEFORE the mux opens FIFOs
  run install -d -m 0755 "${ENV_DIR}"
  sh_c "systemctl reset-failed realsense-mux.service 2>/dev/null || true"
  run systemctl enable realsense-mux.service
  run systemctl restart realsense-mux.service          # (re)open FIFOs with the enlarged buffer
  # node-agent: steady-state control plane for the gateway (FDIR restart / re-probe)
  [ -f "${BUNDLE_DIR}/VERSION" ] && run install -m 0644 "${BUNDLE_DIR}/VERSION" "${ENV_DIR}/node-bundle.version"
  if [ -n "$AGENT_TOKEN" ]; then
    _valid_token "$AGENT_TOKEN"
    write_file 0600 "${ENV_DIR}/node-agent.env" "$(printf 'NODE_AGENT_TOKEN=%s' "$AGENT_TOKEN")"
    log "wrote ${ENV_DIR}/node-agent.env (agent token set — control endpoints now require it)"
  fi
  run systemctl daemon-reload                          # pick up the freshly-installed unit file
  sh_c "systemctl reset-failed camera-node-agent.service 2>/dev/null || true"
  run systemctl enable camera-node-agent.service
  # RESTART, not `enable --now`: a re-deploy must load the freshly-installed agent
  # code, but `enable --now` only *starts* a stopped unit — it never restarts an
  # already-running agent, so new code (e.g. a new endpoint) would silently not load.
  # The agent carries no media (control plane only), so a restart is a sub-second blip.
  run systemctl restart camera-node-agent.service
  log "pipe deployed (mux + node-agent active). Activate streams: bootstrap.sh activate --sensor <s> --rtp-target-host <gw> --rtp-port <p>"
}

cmd_activate() {  # activate ONE stream — uniform across sensors
  _need_root
  [ -n "$SENSOR" ] || die "--sensor required"; _valid_sensor "$SENSOR"
  [ -n "$RTP_TARGET_HOST" ] || die "--rtp-target-host required"; _valid_ipv4 "$RTP_TARGET_HOST"
  [ -n "$RTP_PORT" ] || die "--rtp-port required"; _valid_port "$RTP_PORT"
  run install -d -m 0755 "${ENV_DIR}"
  write_file 0644 "${ENV_DIR}/rs-${SENSOR}.contract.env" \
    "$(printf 'PORT="%s"\nRTP_TARGET_HOST="%s"' "$RTP_PORT" "$RTP_TARGET_HOST")"
  log "wrote ${ENV_DIR}/rs-${SENSOR}.contract.env (PORT=${RTP_PORT} -> ${RTP_TARGET_HOST})"
  # color is the only mux-gated sensor (USB bandwidth opt-in); enabling it needs a
  # mux restart so it starts producing color.fifo. depth/ir are always produced.
  if [ "$SENSOR" = color ]; then
    _set_mux_env RS_ENABLE_COLOR 1
    run systemctl restart realsense-mux.service
  fi
  _wait_fifo "/run/realsense/${SENSOR}.fifo"           # encoder connects once the FIFO flows
  sh_c "systemctl reset-failed rs-stream@${SENSOR}.service 2>/dev/null || true"
  # Unified node lifecycle: the GATEWAY owns the stream lifecycle. Start the encoder now (the gateway
  # asked for activation) but do NOT `enable` autostart — on reboot the gateway's FDIR bring-up
  # restarts desired_up streams once the node is reachable. Autostart would resurrect a stream the
  # gateway has Stopped (desired_up=False), re-creating the node-vs-gateway split this design removes.
  sh_c "systemctl disable rs-stream@${SENSOR}.service 2>/dev/null || true"
  run systemctl start "rs-stream@${SENSOR}.service"
  log "activated ${SENSOR} -> ${RTP_TARGET_HOST}:${RTP_PORT} (gateway-driven; autostart disabled)"
}

cmd_deactivate() {
  _need_root
  [ -n "$SENSOR" ] || die "--sensor required"; _valid_sensor "$SENSOR"
  sh_c "systemctl disable --now rs-stream@${SENSOR}.service 2>/dev/null || true"
  sh_c "systemctl reset-failed rs-stream@${SENSOR}.service 2>/dev/null || true"
  log "deactivated ${SENSOR}"
}

cmd_set_token() {  # rotate the node-agent token: rewrite env + restart ONLY the agent
  _need_root
  [ -n "$AGENT_TOKEN" ] || die "--agent-token required"; _valid_token "$AGENT_TOKEN"
  run install -d -m 0755 "${ENV_DIR}"
  write_file 0600 "${ENV_DIR}/node-agent.env" "$(printf 'NODE_AGENT_TOKEN=%s' "$AGENT_TOKEN")"
  run systemctl restart camera-node-agent.service      # mux untouched — streams keep flowing
  log "rotated node-agent token (agent restarted; mux + encoders untouched)"
}

case "$MODE" in
  probe)       cmd_probe ;;
  deploy)      cmd_deploy ;;
  activate)    cmd_activate ;;
  deactivate)  cmd_deactivate ;;
  set-token)   cmd_set_token ;;
  ""|--help|-h) usage; [ "$MODE" = "" ] && exit 1 || exit 0 ;;
  *)           die "unknown mode '$MODE' (probe|deploy|activate|deactivate|set-token)" ;;
esac
