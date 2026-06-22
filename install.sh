#!/usr/bin/env bash
# janus-camera-page installer — fresh-install bootstrap for Linux hosts.
#
# What it does (in order):
#   1. Detect environment (OS, arch, Pi model, attached cameras)
#   2. Preflight checks (sudo, network, disk space)
#   3. Install system deps (apt: ffmpeg, python3-venv, build-essential, curl, etc.)
#   4. Install Janus Gateway (apt OR build from source if too old)
#   5. Install coturn (apt + minimal config)
#   6. Handle pyrealsense2 (vendored wheel on Pi+Ubuntu24, PyPI on amd64,
#      build-instructions message on other arch)
#   7. Deploy encoder scripts + systemd units (/usr/local/bin + /etc/systemd/system)
#   8. Install camera-page L4 (Python venv in /opt/janus-camera-page)
#   9. Generate secrets (TURN_SECRET, INTERNAL_API_SECRET, JANUS_ADMIN_SECRET)
#  10. Start + enable services
#  11. Verify health endpoints
#  12. Print summary with dashboard URL
#
# Idempotent — safe to re-run. Use --dry-run to preview without making changes.
# Use --skip-COMPONENT to omit a stage (e.g., --skip-janus if already installed).
#
# Supported targets (tier 1, tested):
#   - Raspberry Pi 4/5 on Ubuntu 22.04+/24.04 LTS (arm64)
#   - Generic x86_64 Linux on Ubuntu 22.04+/24.04 LTS or Debian 12+
#
# Tier 2 (works but may need manual pyrealsense2 build):
#   - Other arm64 SBCs
#   - Other Linux distros with apt OR yum/dnf adapter
#
# Run:
#   chmod +x install.sh
#   sudo ./install.sh                 # full install
#   sudo ./install.sh --dry-run       # preview, change nothing
#   sudo ./install.sh --skip-janus    # if you already have Janus
#   sudo ./install.sh --probe-only    # just print environment report and exit

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PREFIX="/opt/janus-camera-page"
BIN_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_DIR="/etc/robot"
STATE_DIR="/var/lib/robot"
PLUGIN_DIR="/etc/robot/plugins.d"
WHEELS_DIR="${REPO_ROOT}/installer/wheels"

# RealSense USB ID (D435/D435i + similar)
REALSENSE_USB_IDS="8086:0b3a 8086:0b07 8086:0b64 8086:0b68"

# Colors (auto-disable if no TTY)
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_GREEN=$'\033[32m'
  C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_RED=''; C_YELLOW=''; C_GREEN=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi

# Flags (set by CLI parse)
DRY_RUN=0
PROBE_ONLY=0
SKIP_JANUS=0
SKIP_JANUS_CONFIGS=0
SKIP_COTURN=0
SKIP_PYREALSENSE=0
SKIP_ENCODER=0
SKIP_CAMERA_PAGE=0
SKIP_SECRETS=0
NO_VERIFY=0
ASSUME_YES=0

# Janus paths (filled by detect_janus_paths)
JANUS_CFG_DIR=""
JANUS_PLUGINS_DIR=""
JANUS_TRANSPORTS_DIR=""

# Detected values (filled by detect_environment)
OS_ID=""
OS_VERSION_ID=""
ARCH=""
IS_RPI=0
RPI_MODEL=""
HAS_REALSENSE=0
REALSENSE_DEVICES=""
V4L2_DEVICES=""
HAS_JANUS=0
JANUS_VERSION=""
HAS_FFMPEG=0
HAS_PYTHON312=0

# ── Logging helpers ───────────────────────────────────────────────────
log()      { printf '%b\n' "${C_BLUE}[install]${C_RESET} $*" >&2; }
log_ok()   { printf '%b\n' "${C_GREEN}[ ok ]${C_RESET} $*" >&2; }
log_warn() { printf '%b\n' "${C_YELLOW}[warn]${C_RESET} $*" >&2; }
log_err()  { printf '%b\n' "${C_RED}[err ]${C_RESET} $*" >&2; }
log_step() { printf '\n%b\n' "${C_BOLD}━━ $* ━━${C_RESET}" >&2; }

die() { log_err "$*"; exit 1; }

run() {
  if [ "${DRY_RUN}" = "1" ]; then
    printf '%b\n' "${C_YELLOW}[dry-run]${C_RESET} $*" >&2
  else
    eval "$@"
  fi
}

# ── CLI parsing ───────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --dry-run             Preview actions without making changes
  --probe-only          Print environment report and exit (no install)
  --skip-janus          Skip Janus install (use existing — also skips configs)
  --skip-janus-configs  Skip Janus jcfg rendering (keep existing configs)
  --skip-coturn         Skip coturn install
  --skip-pyrealsense    Skip pyrealsense2 (color cameras still work)
  --skip-encoder        Skip encoder scripts + systemd units
  --skip-camera-page    Skip camera-page L4 service install
  --skip-secrets        Skip secret generation (re-use existing /etc/robot/camera-secrets.env)
  --no-verify           Skip post-install health check
  --yes, -y             Auto-confirm prompts (non-interactive mode)
  --help, -h            Show this help

Examples:
  sudo $0                              # full install
  sudo $0 --dry-run                    # preview only
  sudo $0 --skip-janus --skip-coturn   # only deploy app side
  $0 --probe-only                      # report environment, no install
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)             DRY_RUN=1 ;;
    --probe-only)          PROBE_ONLY=1 ;;
    --skip-janus)          SKIP_JANUS=1; SKIP_JANUS_CONFIGS=1 ;;   # skip both unless explicitly enabled
    --skip-janus-configs)  SKIP_JANUS_CONFIGS=1 ;;
    --skip-coturn)         SKIP_COTURN=1 ;;
    --skip-pyrealsense)    SKIP_PYREALSENSE=1 ;;
    --skip-encoder)        SKIP_ENCODER=1 ;;
    --skip-camera-page)    SKIP_CAMERA_PAGE=1 ;;
    --skip-secrets)        SKIP_SECRETS=1 ;;
    --no-verify)           NO_VERIFY=1 ;;
    --yes|-y)              ASSUME_YES=1 ;;
    --help|-h)             usage; exit 0 ;;
    *)                     log_err "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

# ── Environment detection ─────────────────────────────────────────────
detect_environment() {
  log_step "Detecting environment"

  # OS
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION_ID="${VERSION_ID:-unknown}"
    log "OS: ${PRETTY_NAME:-$OS_ID $OS_VERSION_ID}"
  else
    log_warn "/etc/os-release not found; OS detection failed"
    OS_ID="unknown"; OS_VERSION_ID="unknown"
  fi

  # Arch
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  log "Architecture: ${ARCH}"

  # Pi detection
  if [ -f /proc/device-tree/model ]; then
    RPI_MODEL="$(tr -d '\0' < /proc/device-tree/model)"
    if echo "${RPI_MODEL}" | grep -qi "raspberry pi"; then
      IS_RPI=1
      log "Hardware: ${RPI_MODEL}"
    fi
  fi
  [ ${IS_RPI} -eq 0 ] && log "Hardware: generic (not Raspberry Pi)"

  # RealSense USB probe
  if command -v lsusb >/dev/null 2>&1; then
    REALSENSE_DEVICES=""
    for id in ${REALSENSE_USB_IDS}; do
      if lsusb -d "${id}" 2>/dev/null | grep -q "${id}"; then
        REALSENSE_DEVICES+=" ${id}"
        HAS_REALSENSE=1
      fi
    done
    if [ ${HAS_REALSENSE} -eq 1 ]; then
      log "RealSense detected:${REALSENSE_DEVICES}"
    else
      log "RealSense: none attached"
    fi
  else
    log_warn "lsusb not available — RealSense detection skipped"
  fi

  # V4L2 devices
  if command -v v4l2-ctl >/dev/null 2>&1; then
    V4L2_DEVICES="$(v4l2-ctl --list-devices 2>/dev/null | grep -E '^\s*/dev/video' | head -5 | tr -s ' ' | xargs || true)"
    if [ -n "${V4L2_DEVICES}" ]; then
      log "V4L2 devices: ${V4L2_DEVICES}"
    else
      log "V4L2: no video devices found"
    fi
  else
    # Fallback: just list /dev/video*
    V4L2_DEVICES="$(ls /dev/video* 2>/dev/null | tr '\n' ' ' || true)"
    [ -n "${V4L2_DEVICES}" ] && log "V4L2 devices (raw): ${V4L2_DEVICES}"
  fi

  # Janus probe
  if command -v janus >/dev/null 2>&1; then
    HAS_JANUS=1
    JANUS_VERSION="$(janus --version 2>&1 | head -1 || echo unknown)"
    log "Janus: installed (${JANUS_VERSION})"
  elif [ -x /opt/janus/bin/janus ]; then
    HAS_JANUS=1
    JANUS_VERSION="$(/opt/janus/bin/janus --version 2>&1 | head -1 || echo unknown)"
    log "Janus: installed at /opt/janus (${JANUS_VERSION})"
  else
    log "Janus: not installed"
  fi

  # ffmpeg
  if command -v ffmpeg >/dev/null 2>&1; then
    HAS_FFMPEG=1
    log "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
  else
    log "ffmpeg: not installed"
  fi

  # Python 3.12
  if command -v python3.12 >/dev/null 2>&1; then
    HAS_PYTHON312=1
    log "Python 3.12: $(python3.12 --version 2>&1)"
  elif python3 --version 2>&1 | grep -q "Python 3.1[2-9]"; then
    HAS_PYTHON312=1
    log "Python 3.12+: $(python3 --version 2>&1)"
  else
    log "Python 3.12: not installed (have $(python3 --version 2>&1 || echo none))"
  fi
}

# ── Environment compatibility tier ────────────────────────────────────
classify_compat_tier() {
  # Returns tier number: 1 (fully supported, vendored wheels), 2 (works, may need manual steps), 3 (unsupported)
  case "${OS_ID}" in
    ubuntu)
      case "${OS_VERSION_ID}" in
        22.04|24.04) echo 1; return ;;
        *) echo 2; return ;;
      esac
      ;;
    debian)
      case "${OS_VERSION_ID}" in
        12) echo 1; return ;;
        11) echo 2; return ;;
      esac
      ;;
  esac
  echo 3
}

# ── Preflight ─────────────────────────────────────────────────────────
preflight() {
  log_step "Preflight checks"

  # sudo (skip if dry-run-only)
  if [ "${DRY_RUN}" != "1" ] && [ "${PROBE_ONLY}" != "1" ]; then
    if [ "$(id -u)" -ne 0 ]; then
      die "Installer must run as root (try: sudo $0)"
    fi
    log_ok "Running as root"
  fi

  # Disk space (need ~500MB free in /opt)
  if [ "${PROBE_ONLY}" != "1" ]; then
    local free_mb
    free_mb="$(df -BM /opt 2>/dev/null | awk 'NR==2 {gsub("M","",$4); print $4}' || echo 0)"
    if [ -n "${free_mb}" ] && [ "${free_mb}" -lt 500 ]; then
      log_warn "Less than 500MB free in /opt (${free_mb}MB) — install may fail"
    else
      log_ok "Disk space: ${free_mb:-?}MB free in /opt"
    fi
  fi

  # OS supported tier
  local tier; tier="$(classify_compat_tier)"
  case "${tier}" in
    1) log_ok "OS tier 1 (fully supported, vendored wheels available)" ;;
    2) log_warn "OS tier 2 (works, but pyrealsense2 may need manual build)" ;;
    3) log_warn "OS tier 3 (unsupported — proceed at your own risk)" ;;
  esac

  # Network — required for apt + pip
  if [ "${DRY_RUN}" != "1" ] && [ "${PROBE_ONLY}" != "1" ]; then
    if ! curl -fsS --max-time 5 https://archive.ubuntu.com >/dev/null 2>&1 \
      && ! curl -fsS --max-time 5 https://pypi.org >/dev/null 2>&1; then
      log_warn "Network connectivity to archive.ubuntu.com / pypi.org seems off"
    else
      log_ok "Network reachable"
    fi
  fi
}

# ── Confirm before destructive ops ────────────────────────────────────
confirm() {
  [ "${ASSUME_YES}" = "1" ] && return 0
  [ "${DRY_RUN}" = "1" ] && return 0
  [ "${PROBE_ONLY}" = "1" ] && return 0
  local prompt="${1:-Proceed?}"
  printf '%b ' "${C_BOLD}${prompt} [y/N]${C_RESET}" >&2
  read -r answer
  case "${answer}" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ── System deps via apt ───────────────────────────────────────────────
install_system_deps() {
  log_step "Installing system dependencies (apt)"
  run "apt-get update -qq"
  local pkgs="ffmpeg curl ca-certificates build-essential pkg-config \
              python3 python3-venv python3-pip python3-dev \
              v4l-utils usbutils libssl-dev libudev-dev libusb-1.0-0-dev \
              cmake git"
  run "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ${pkgs}"
  log_ok "System deps installed"
}

# ── Janus ─────────────────────────────────────────────────────────────
# Detect Janus install path (apt → /etc/janus, source → /opt/janus/etc/janus).
# Sets globals: JANUS_CFG_DIR, JANUS_PLUGINS_DIR, JANUS_TRANSPORTS_DIR
detect_janus_paths() {
  if [ -d /opt/janus/etc/janus ]; then
    # Source-built (./configure --prefix=/opt/janus)
    JANUS_CFG_DIR=/opt/janus/etc/janus
    JANUS_PLUGINS_DIR=/opt/janus/lib/janus/plugins
    JANUS_TRANSPORTS_DIR=/opt/janus/lib/janus/transports
    return
  fi
  if [ ! -d /etc/janus ]; then
    JANUS_CFG_DIR=""
    JANUS_PLUGINS_DIR=""
    JANUS_TRANSPORTS_DIR=""
    return
  fi
  # apt-installed — handle multiarch (/usr/lib/<triplet>/janus/)
  JANUS_CFG_DIR=/etc/janus
  local multiarch=""
  if command -v dpkg-architecture >/dev/null 2>&1; then
    multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || true)"
  fi
  # Try multiarch path first, fall back to non-multiarch, then probe via find
  for candidate in \
      "/usr/lib/${multiarch}/janus" \
      "/usr/lib/janus" \
      "$(dirname "$(find /usr/lib -name 'libjanus_streaming*.so' 2>/dev/null | head -1)" 2>/dev/null)"; do
    if [ -n "${candidate}" ] && [ -d "${candidate}/plugins" ]; then
      JANUS_PLUGINS_DIR="${candidate}/plugins"
      JANUS_TRANSPORTS_DIR="${candidate}/transports"
      return
    fi
  done
  # Last resort — set best guess (may fail at Janus start, but with a clear error)
  JANUS_PLUGINS_DIR="/usr/lib/${multiarch:-janus}/janus/plugins"
  JANUS_TRANSPORTS_DIR="/usr/lib/${multiarch:-janus}/janus/transports"
}

install_janus() {
  [ "${SKIP_JANUS}" = "1" ] && { log "Skipping Janus install (--skip-janus)"; return; }
  if [ "${HAS_JANUS}" = "1" ]; then
    log "Janus already installed (${JANUS_VERSION}) — skipping apt install"
  else
    log_step "Installing Janus Gateway"
    # Try apt first (Ubuntu 22.04+ has janus 1.0.1+, Debian 12 has 1.0.4)
    if apt-cache show janus >/dev/null 2>&1; then
      log "Installing janus from apt"
      run "DEBIAN_FRONTEND=noninteractive apt-get install -y janus"
      log_ok "Janus installed via apt"
    else
      log_warn "Janus not in apt repo — operator must build from source."
      cat >&2 <<EOF
${C_YELLOW}Action required: Janus is not available via apt on this system.${C_RESET}

Build from source:
  git clone https://github.com/meetecho/janus-gateway.git
  cd janus-gateway
  sh autogen.sh && ./configure --prefix=/opt/janus
  make && sudo make install && sudo make configs

Then re-run the installer with --skip-janus.

EOF
      die "Janus install requires manual step — see above"
    fi
  fi

  # Detect where Janus is installed (apt vs source)
  detect_janus_paths
  if [ -z "${JANUS_CFG_DIR}" ]; then
    die "Janus install completed but config dir not found at /etc/janus or /opt/janus/etc/janus"
  fi
  log "Janus config dir: ${JANUS_CFG_DIR}"

  # Deploy admin CLIs (from host_infra/roles/janus/files/)
  local janus_files="${REPO_ROOT}/host_infra/roles/janus/files"
  if [ -d "${janus_files}" ]; then
    log "Deploying Janus admin CLIs"
    [ -f "${janus_files}/janus-admin.py" ] && \
      run "install -m 0755 ${janus_files}/janus-admin.py ${BIN_DIR}/janus-admin"
    [ -f "${janus_files}/janus-nat-updater.sh" ] && \
      run "install -m 0755 ${janus_files}/janus-nat-updater.sh ${BIN_DIR}/janus-nat-updater"
    [ -f "${janus_files}/janus-turn-rotator.py" ] && \
      run "install -m 0755 ${janus_files}/janus-turn-rotator.py ${BIN_DIR}/janus-turn-rotator"
    # turn-rotator systemd timer (not auto-enabled — operator opts in)
    [ -f "${janus_files}/janus-turn-rotator.service" ] && \
      run "install -m 0644 ${janus_files}/janus-turn-rotator.service ${SYSTEMD_DIR}/janus-turn-rotator.service"
    [ -f "${janus_files}/janus-turn-rotator.timer" ] && \
      run "install -m 0644 ${janus_files}/janus-turn-rotator.timer ${SYSTEMD_DIR}/janus-turn-rotator.timer"
    log_ok "Janus admin CLIs deployed (turn-rotator timer NOT enabled — opt in via systemctl enable janus-turn-rotator.timer)"
  fi
}

# ── Janus configs: delegate to jcfg_renderer (single source of truth) ─
install_janus_configs() {
  [ "${SKIP_JANUS_CONFIGS}" = "1" ] && { log "Skipping Janus configs (--skip-janus-configs)"; return; }
  log_step "Installing Janus configs via jcfg_renderer"

  detect_janus_paths
  [ -z "${JANUS_CFG_DIR}" ] && { log_warn "No Janus config dir found — skip"; return; }

  # Transport configs (non-templated, copy as-is)
  local tpl_dir="${REPO_ROOT}/deploy/janus/etc"
  if [ -d "${tpl_dir}" ]; then
    if [ -f "${tpl_dir}/janus.transport.http.jcfg" ]; then
      run "install -m 0644 ${tpl_dir}/janus.transport.http.jcfg ${JANUS_CFG_DIR}/janus.transport.http.jcfg"
    fi
    if [ -f "${tpl_dir}/janus.transport.websockets.jcfg" ]; then
      run "install -m 0644 ${tpl_dir}/janus.transport.websockets.jcfg ${JANUS_CFG_DIR}/janus.transport.websockets.jcfg"
    fi
  fi

  # Render *.template files via Python module — same logic that admin
  # page /apply uses. DRY: single source of truth for substitution.
  local renderer_python="${INSTALL_PREFIX}/venv/bin/python"
  if [ ! -x "${renderer_python}" ]; then
    # Fall back to system python if the venv has not been created yet (installer ordering bug)
    renderer_python="$(command -v python3.12 || command -v python3)"
  fi

  if [ "${DRY_RUN}" = "1" ]; then
    log "[dry-run] would run: ${renderer_python} -m app.services.jcfg_renderer render"
  else
    if [ -d "${INSTALL_PREFIX}/app" ]; then
      (cd "${INSTALL_PREFIX}" && "${renderer_python}" -m app.services.jcfg_renderer render) || {
        log_err "jcfg_renderer failed — Janus configs not rendered"
        log "Run manually: cd ${INSTALL_PREFIX} && ${renderer_python} -m app.services.jcfg_renderer render"
        return 1
      }
    else
      log_warn "${INSTALL_PREFIX}/app not found — skip render (install-camera-page didn't run)"
      return 0
    fi
  fi

  log_ok "Janus configs rendered via jcfg_renderer. ${C_YELLOW}REMEMBER:${C_RESET} set nat_1_1_mapping via admin_config page OR edit ${JANUS_CFG_DIR}/janus.jcfg directly"
}

# ── textroom relay sidecar (FastAPI process on :9000) ─────────────────
install_relay() {
  [ "${SKIP_CAMERA_PAGE}" = "1" ] && { log "Skipping relay (--skip-camera-page also disables relay)"; return; }
  log_step "Installing textroom relay (back-channel sidecar)"

  if [ "${DRY_RUN}" = "1" ]; then
    log "[dry-run] would write ${SYSTEMD_DIR}/janus-textroom-relay.service"
  else
    cat > "${SYSTEMD_DIR}/janus-textroom-relay.service" <<EOF
[Unit]
Description=Janus TextRoom → topic-relay sidecar (FastAPI)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=${INSTALL_PREFIX}
EnvironmentFile=-${CONFIG_DIR}/camera-secrets.env
EnvironmentFile=-${CONFIG_DIR}/relay.env
Environment="PYTHONUNBUFFERED=1"
Environment="LOG_LEVEL=INFO"
Environment="QUEUE_MAX=50"
Environment="STATS_EVERY_S=2.0"
ExecStart=${INSTALL_PREFIX}/venv/bin/uvicorn textroom_relay:app --host 0.0.0.0 --port 9000 --workers 1 --no-access-log --proxy-headers
Restart=on-failure
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF
  fi
  run "systemctl daemon-reload"
  log_ok "textroom relay unit installed"
}

# ── coturn ────────────────────────────────────────────────────────────
install_coturn() {
  [ "${SKIP_COTURN}" = "1" ] && { log "Skipping coturn install (--skip-coturn)"; return; }
  log_step "Installing coturn"

  if command -v turnserver >/dev/null 2>&1; then
    log "coturn already installed — skipping apt install"
  else
    run "DEBIAN_FRONTEND=noninteractive apt-get install -y coturn"
    log_ok "coturn installed"
  fi

  # Minimal config — operator edits later via /etc/turnserver.conf
  if [ ! -f /etc/turnserver.conf.installer-backup ] && [ -f /etc/turnserver.conf ]; then
    run "cp /etc/turnserver.conf /etc/turnserver.conf.installer-backup"
  fi

  log "Config: /etc/turnserver.conf — set use-auth-secret + static-auth-secret"
  log "        Realm + min-port/max-port also needed for production"
  log_warn "coturn NOT auto-enabled — operator must configure + enable manually"
}

# ── pyrealsense2 (the tricky one) ─────────────────────────────────────
install_pyrealsense() {
  [ "${SKIP_PYREALSENSE}" = "1" ] && { log "Skipping pyrealsense2 (--skip-pyrealsense)"; return; }

  log_step "Installing pyrealsense2 (depth camera SDK)"

  if [ "${HAS_REALSENSE}" = "0" ]; then
    log "No RealSense device attached — pyrealsense2 not installed."
    log "Color cameras work without it. To enable depth/IR streams:"
    log "  1. Connect D435/D435i USB"
    log "  2. Re-run: $0 --skip-janus --skip-coturn --skip-encoder --skip-camera-page"
    return
  fi

  # Decision tree:
  #   1. Try our vendored wheel (if Pi+Ubuntu24 arm64)
  #   2. Try PyPI (works on amd64 Linux from Intel)
  #   3. Fallback: print build instructions
  local pip_bin="${INSTALL_PREFIX}/venv/bin/pip"
  if [ ! -x "${pip_bin}" ] && [ "${DRY_RUN}" != "1" ]; then
    log_warn "camera-page venv not created yet — pyrealsense2 install deferred"
    log "  Will install during camera-page install step"
    return
  fi

  local tier; tier="$(classify_compat_tier)"
  local vendored_wheel=""

  # Look for vendored wheel matching this OS/arch
  if [ -d "${WHEELS_DIR}" ]; then
    case "${ARCH}-${OS_ID}-${OS_VERSION_ID}" in
      arm64-ubuntu-24.04|aarch64-ubuntu-24.04)
        vendored_wheel="$(ls "${WHEELS_DIR}"/pyrealsense2-*-cp312-cp312-linux_aarch64.whl 2>/dev/null | head -1 || true)"
        ;;
      arm64-ubuntu-22.04|aarch64-ubuntu-22.04)
        vendored_wheel="$(ls "${WHEELS_DIR}"/pyrealsense2-*-cp310-cp310-linux_aarch64.whl 2>/dev/null | head -1 || true)"
        ;;
    esac
  fi

  if [ -n "${vendored_wheel}" ] && [ -f "${vendored_wheel}" ]; then
    log "Installing vendored wheel: $(basename "${vendored_wheel}")"
    run "${pip_bin} install '${vendored_wheel}' numpy"
    log_ok "pyrealsense2 installed from vendored wheel"
    return
  fi

  # Try PyPI (works on amd64 Linux)
  if [ "${ARCH}" = "amd64" ] || [ "${ARCH}" = "x86_64" ]; then
    log "Trying PyPI pyrealsense2 (amd64 has prebuilt wheels)"
    if run "${pip_bin} install pyrealsense2 numpy"; then
      log_ok "pyrealsense2 installed from PyPI"
      return
    fi
    log_warn "PyPI install failed — falling back to manual instructions"
  fi

  # Fallback: manual build instructions
  log_err "pyrealsense2 cannot be auto-installed for this platform."
  cat >&2 <<EOF
${C_YELLOW}Action required: pyrealsense2 must be built from source.${C_RESET}

Quick recipe (arm64 Ubuntu 22.04/24.04):

  sudo apt install -y cmake build-essential python3-dev libssl-dev libusb-1.0-0-dev libudev-dev pkg-config
  git clone https://github.com/IntelRealSense/librealsense.git
  cd librealsense
  mkdir build && cd build
  cmake .. -DBUILD_PYTHON_BINDINGS=ON -DPYTHON_EXECUTABLE=$(which python3) -DBUILD_EXAMPLES=OFF
  make -j$(nproc)
  sudo make install
  sudo cp config/99-realsense-libusb.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules && sudo udevadm trigger

  # Python module is built but pip install separately:
  cd ../wrappers/python
  ${pip_bin} install .

Then re-run the installer with --skip-pyrealsense:
  sudo $0 --skip-janus --skip-coturn --skip-pyrealsense

If you build successfully, please contribute the wheel:
  ${pip_bin} wheel pyrealsense2 -w installer/wheels/

EOF
  log_warn "Continuing install without depth support — color cameras will still work"
}

# ── Encoder scripts + systemd units ───────────────────────────────────
install_encoder() {
  [ "${SKIP_ENCODER}" = "1" ] && { log "Skipping encoder install"; return; }
  log_step "Installing encoder scripts + systemd units"

  local enc_files="${REPO_ROOT}/host_infra/roles/encoder/files"
  if [ ! -d "${enc_files}" ]; then
    die "host_infra/roles/encoder/files not found at ${enc_files}"
  fi

  # Adapter scripts (all rtp-*.sh + producers)
  for script in rtp-v4l2.sh rtp-rtsp.sh rs-stream.sh realsense-mux.py; do
    if [ -f "${enc_files}/${script}" ]; then
      run "install -m 0755 ${enc_files}/${script} ${BIN_DIR}/${script%.py}"
    fi
  done

  # Admin CLIs
  run "install -m 0755 ${enc_files}/encoder-admin.py ${BIN_DIR}/encoder-admin"
  run "install -m 0755 ${enc_files}/camera-admin.py ${BIN_DIR}/camera-admin"

  # systemd units
  for unit in rtp-v4l2@.service rtp-rtsp@.service \
              rs-stream@.service realsense-mux.service; do
    if [ -f "${enc_files}/${unit}" ]; then
      run "install -m 0644 ${enc_files}/${unit} ${SYSTEMD_DIR}/${unit}"
    fi
  done

  # Example env files (operator copies + edits)
  run "mkdir -p ${CONFIG_DIR}"
  for ex in rtp-v4l2-example.tuning.env rtp-v4l2-example.contract.env \
            rtp-rtsp-example.tuning.env rtp-rtsp-example.contract.env; do
    if [ -f "${enc_files}/${ex}" ]; then
      run "install -m 0644 ${enc_files}/${ex} ${CONFIG_DIR}/${ex}"
    fi
  done

  # Reload systemd
  run "systemctl daemon-reload"
  log_ok "Encoder scripts + units installed (10 scripts, 5 unit templates)"
}

# ── camera-page L4 (FastAPI service) ──────────────────────────────────
install_camera_page() {
  [ "${SKIP_CAMERA_PAGE}" = "1" ] && { log "Skipping camera-page install"; return; }
  log_step "Installing camera-page L4 (FastAPI service)"

  run "mkdir -p ${INSTALL_PREFIX} ${STATE_DIR} ${PLUGIN_DIR}"

  # Copy source tree.
  # NB: keep deploy/janus/etc/ — jcfg_renderer reads templates at runtime
  # for the admin_config /apply endpoint. k8s/Helm manifests not needed.
  log "Copying source to ${INSTALL_PREFIX}/"
  run "rsync -a --exclude='tests/' --exclude='docs/' \
       --exclude='deploy/k8s/' --exclude='deploy/helm/' \
       --exclude='installer/' --exclude='__pycache__/' --exclude='.git*' \
       --exclude='node_modules/' --exclude='.venv/' \
       ${REPO_ROOT}/ ${INSTALL_PREFIX}/"

  # Python venv
  log "Creating Python venv"
  local python_bin="python3"
  command -v python3.12 >/dev/null && python_bin="python3.12"
  run "${python_bin} -m venv ${INSTALL_PREFIX}/venv"
  run "${INSTALL_PREFIX}/venv/bin/pip install --upgrade pip wheel setuptools"
  run "${INSTALL_PREFIX}/venv/bin/pip install -r ${INSTALL_PREFIX}/requirements.txt"
  log_ok "Python venv + deps installed"

  # Now do pyrealsense2 (deferred from earlier — venv exists now)
  install_pyrealsense

  # systemd unit
  cat > /tmp/janus-camera-page.service <<EOF
[Unit]
Description=janus-camera-page L4 FastAPI service
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_PREFIX}
EnvironmentFile=-${CONFIG_DIR}/camera-secrets.env
EnvironmentFile=-${CONFIG_DIR}/camera-page.env
ExecStart=${INSTALL_PREFIX}/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8900 --workers 1
Restart=on-failure
RestartSec=3
User=root
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF
  if [ "${DRY_RUN}" != "1" ]; then
    mv /tmp/janus-camera-page.service "${SYSTEMD_DIR}/janus-camera-page.service"
  else
    log "[dry-run] would write ${SYSTEMD_DIR}/janus-camera-page.service"
    rm -f /tmp/janus-camera-page.service
  fi
  run "systemctl daemon-reload"
  log_ok "camera-page systemd unit installed"

  # Track A: seed the non-secret runtime-tunable env (ICE_POLICY, TURN_CRED_TTL)
  # ONLY-IF-ABSENT — never clobber operator/runtime-tuned values. The L4 drop-in
  # sources it via EnvironmentFile=. (The drop-in itself is deployment-specific —
  # hardcoded paths — and is installed by the deployment's own step, not here.)
  if [ "${DRY_RUN}" != "1" ]; then
    if [ ! -f "${CONFIG_DIR}/rs-runtime.env" ]; then
      install -m 0644 /dev/stdin "${CONFIG_DIR}/rs-runtime.env" <<'RSRT'
# Non-secret runtime-tunable knobs for the L4 service (writable; loaded via the
# janus-camera-page drop-in EnvironmentFile). Allowlist: ICE_POLICY, TURN_CRED_TTL only.
ICE_POLICY=relay
TURN_CRED_TTL=3600
RSRT
      log_ok "seeded ${CONFIG_DIR}/rs-runtime.env"
    else
      log "rs-runtime.env exists — preserving operator-tuned values"
    fi
  else
    log "[dry-run] would seed ${CONFIG_DIR}/rs-runtime.env if absent"
  fi
}

# ── Secret generation ─────────────────────────────────────────────────
generate_secrets() {
  [ "${SKIP_SECRETS}" = "1" ] && { log "Skipping secret generation"; return; }
  log_step "Generating secrets"

  local secret_file="${CONFIG_DIR}/camera-secrets.env"
  if [ -f "${secret_file}" ]; then
    log "${secret_file} already exists — leaving in place (use --skip-secrets to suppress this message)"
    return
  fi

  run "mkdir -p ${CONFIG_DIR}"
  if [ "${DRY_RUN}" = "1" ]; then
    log "[dry-run] would create ${secret_file} with auto-generated secrets"
    return
  fi

  umask 077
  # Generate with base64url (Janus textroom secret format) for compatibility
  cat > "${secret_file}" <<EOF
# Auto-generated by install.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Edit if you need to rotate — services pick up changes on restart.

# TURN (coturn shared secret)
TURN_SHARED_SECRET=$(openssl rand -hex 32)
TURN_HOST=$(hostname -I | awk '{print $1}')
TURN_REALM=$(hostname).local

# Local network addresses — explicit config, derived from the system (never a
# hardcoded LAN address in source). HOST_LAN_IP is this host's address for CSP +
# NAT URLs, taken from the default route's source IP. On a MULTI-HOMED host this
# is the egress interface; set HOST_LAN_IP explicitly to the operational LAN
# (e.g. a camera-LAN bridge) if that differs.
HOST_LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1)
# DEPTH_CAM_URL: a REMOTE depth-camera node cannot be auto-detected — set it
# explicitly for depth deployments. Example:
# DEPTH_CAM_URL=http://192.168.1.55:8900

# Janus admin API (Janus jcfg admin_secret + camera-page JANUS_ADMIN_URL auth)
JANUS_ADMIN_SECRET=$(openssl rand -hex 32)

# Streaming plugin admin key (for dynamic mountpoint create/destroy)
STREAMING_ADMIN_KEY=$(openssl rand 32 | base64 | tr '+/' '-_' | tr -d '=')

# Streaming plugin per-mountpoint secret (for static rgb-rtp default mountpoint)
STREAMING_RGB_MP_SECRET=$(openssl rand 32 | base64 | tr '+/' '-_' | tr -d '=')

# TextRoom room secret (managing room 1000 via Janus admin)
TEXTROOM_ROOM_SECRET=$(openssl rand 32 | base64 | tr '+/' '-_' | tr -d '=')

# Internal API secret (HMAC auth between camera-page and textroom-relay sidecar)
INTERNAL_API_SECRET=$(openssl rand -hex 32)
EOF
  chmod 600 "${secret_file}"
  log_ok "Secrets generated → ${secret_file} (6 keys, mode 0600)"
}

# ── Service start ─────────────────────────────────────────────────────
start_services() {
  [ "${PROBE_ONLY}" = "1" ] && return
  log_step "Starting services"

  if [ "${SKIP_JANUS}" = "0" ] || [ "${HAS_JANUS}" = "1" ]; then
    # Restart (not start) so new configs picked up if Janus was already running
    run "systemctl enable janus.service 2>/dev/null || systemctl enable janus 2>/dev/null || true"
    run "systemctl restart janus.service 2>/dev/null || systemctl restart janus 2>/dev/null || true"
  fi

  if [ "${SKIP_CAMERA_PAGE}" = "0" ]; then
    run "systemctl enable --now janus-textroom-relay.service"
    run "systemctl enable --now janus-camera-page.service"
  fi

  log_ok "Services enabled + started"
}

# ── Verification ──────────────────────────────────────────────────────
verify() {
  [ "${NO_VERIFY}" = "1" ] && { log "Skipping verify (--no-verify)"; return; }
  [ "${DRY_RUN}" = "1" ] && return
  [ "${PROBE_ONLY}" = "1" ] && return

  log_step "Verifying install"
  sleep 3

  local ok=1

  if [ "${SKIP_CAMERA_PAGE}" = "0" ]; then
    if curl -fsS --max-time 5 http://127.0.0.1:8900/livez >/dev/null; then
      log_ok "camera-page /livez 200"
    else
      log_err "camera-page /livez NOT responding"
      ok=0
    fi
    # relay sidecar — the process listens but has no default root route; treat it as
    # OK if TCP accepts connections (curl gets ANY HTTP response, even 404)
    local relay_code
    relay_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:9000/ 2>/dev/null || echo 000)"
    if [ "${relay_code}" != "000" ]; then
      log_ok "textroom relay :9000 responding (HTTP ${relay_code})"
    else
      log_warn "textroom relay :9000 not responding — back-channel data will not flow"
      ok=0
    fi
  fi

  if [ "${SKIP_JANUS}" = "0" ] || [ "${HAS_JANUS}" = "1" ]; then
    if curl -fsS --max-time 5 http://127.0.0.1:8088/janus/info >/dev/null; then
      log_ok "Janus /janus/info 200"
    else
      log_warn "Janus not responding on :8088 — check janus.service logs"
      ok=0
    fi
    # Verify configs got installed properly
    if [ -n "${JANUS_CFG_DIR}" ] && [ -f "${JANUS_CFG_DIR}/janus.plugin.streaming.jcfg" ]; then
      log_ok "streaming.jcfg installed at ${JANUS_CFG_DIR}"
    fi
    if [ -n "${JANUS_CFG_DIR}" ] && [ -f "${JANUS_CFG_DIR}/janus.plugin.textroom.jcfg" ]; then
      log_ok "textroom.jcfg installed at ${JANUS_CFG_DIR}"
    fi
  fi

  if [ "${ok}" = "1" ]; then
    log_ok "All checks passed"
  else
    log_warn "Some checks failed — see journalctl -u janus-camera-page -u janus -u janus-textroom-relay"
  fi
}

# ── Summary ───────────────────────────────────────────────────────────
print_summary() {
  [ "${PROBE_ONLY}" = "1" ] && return
  local ip; ip="$(hostname -I | awk '{print $1}')"
  cat >&2 <<EOF

${C_GREEN}${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}
${C_BOLD}janus-camera-page install complete${C_RESET}

Dashboard:        http://${ip}:8900/color_camera
Health:           http://${ip}:8900/livez
Sensor types:     http://${ip}:8900/api/v1/color_camera/sensor_types
Janus admin:      http://${ip}:8088/janus/info

Secrets:          ${CONFIG_DIR}/camera-secrets.env (mode 0600, 6 keys)
Code:             ${INSTALL_PREFIX}/
Plugins dir:      ${PLUGIN_DIR}/
Janus configs:    ${JANUS_CFG_DIR:-not detected}/
                  ├─ janus.jcfg                       (main — nat_1_1_mapping ☆)
                  ├─ janus.plugin.streaming.jcfg      (rgb-rtp static mountpoint)
                  ├─ janus.plugin.textroom.jcfg       (back-channel room-1000)
                  └─ streams.d/                       (dynamic mountpoints)

Next steps:
  1. ${C_BOLD}Edit ${JANUS_CFG_DIR:-/etc/janus}/janus.jcfg${C_RESET} — set nat_1_1_mapping to the public IP
  2. Edit /etc/turnserver.conf — realm + use-auth-secret matching TURN_SHARED_SECRET
  3. Restart Janus: sudo systemctl restart janus
  4. Start a camera: see docs/TUTORIAL_USB_WEBCAM.md
  5. Logs: journalctl -u janus-camera-page -u janus -u janus-textroom-relay -f

Troubleshooting: docs/OPERATOR_RUNBOOK.md
${C_GREEN}${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}
EOF
}

# ── Main orchestrator ─────────────────────────────────────────────────
main() {
  log_step "janus-camera-page installer"
  log "Repo: ${REPO_ROOT}"
  [ "${DRY_RUN}" = "1" ] && log_warn "DRY-RUN mode — no changes will be made"

  detect_environment

  if [ "${PROBE_ONLY}" = "1" ]; then
    log_step "Probe-only mode — exiting"
    local rs_state v4l_state janus_state ffmpeg_state py_state
    [ "${HAS_REALSENSE}" = "1" ] && rs_state="yes (${REALSENSE_DEVICES# })" || rs_state="no"
    v4l_state="${V4L2_DEVICES:-none}"
    [ "${HAS_JANUS}" = "1" ] && janus_state="installed (${JANUS_VERSION})" || janus_state="not installed"
    [ "${HAS_FFMPEG}" = "1" ] && ffmpeg_state="installed" || ffmpeg_state="not installed"
    [ "${HAS_PYTHON312}" = "1" ] && py_state="yes" || py_state="no"
    cat >&2 <<EOF

Environment summary:
  OS:          ${OS_ID} ${OS_VERSION_ID} (${ARCH})
  Hardware:    ${RPI_MODEL:-generic}
  Tier:        $(classify_compat_tier)
  RealSense:   ${rs_state}
  V4L2:        ${v4l_state}
  Janus:       ${janus_state}
  ffmpeg:      ${ffmpeg_state}
  Python 3.12: ${py_state}

To install: sudo $0
EOF
    exit 0
  fi

  preflight

  if ! confirm "Continue with install on ${OS_ID} ${OS_VERSION_ID} ${ARCH}?"; then
    log "Aborted"; exit 0
  fi

  install_system_deps
  install_janus              # apt install + admin CLIs (no jcfg yet)
  install_coturn
  install_encoder
  install_camera_page        # creates venv + L4 systemd unit
  generate_secrets           # MUST run before install_janus_configs
  install_janus_configs      # renders templates with secrets → JANUS_CFG_DIR
  install_relay              # textroom relay sidecar (depends on venv)
  start_services
  verify
  print_summary
}

main "$@"
