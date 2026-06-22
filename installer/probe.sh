#!/usr/bin/env bash
# probe.sh — standalone environment + hardware probe for janus-camera-page.
#
# Faster way to ask "what can this machine do?" without diving into the full installer.
# Read-only — never makes changes.
#
# Output: human-readable summary to stderr + machine-parseable JSON to stdout
# (if --json flag).
#
# Usage:
#   ./probe.sh             # human report
#   ./probe.sh --json      # JSON for programs
#   ./probe.sh --verbose   # extra detail (V4L2 formats, RealSense modes)

set -euo pipefail

JSON_OUT=0
VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON_OUT=1 ;;
    --verbose|-v) VERBOSE=1 ;;
    --help|-h)
      cat <<EOF
Usage: $0 [--json] [--verbose]
  --json     emit machine-readable JSON to stdout
  --verbose  include V4L2 formats, RealSense modes, network details
EOF
      exit 0 ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
  shift
done

# ── Collect facts ─────────────────────────────────────────────────────
os_id="unknown"; os_version="unknown"; os_pretty="unknown"
if [ -f /etc/os-release ]; then
  . /etc/os-release
  os_id="${ID:-unknown}"
  os_version="${VERSION_ID:-unknown}"
  os_pretty="${PRETTY_NAME:-${os_id} ${os_version}}"
fi

arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
kernel="$(uname -r)"
hostname="$(hostname)"

is_rpi=false
rpi_model=""
if [ -f /proc/device-tree/model ]; then
  rpi_model="$(tr -d '\0' < /proc/device-tree/model)"
  if echo "${rpi_model}" | grep -qi "raspberry pi"; then
    is_rpi=true
  fi
fi

# RealSense USB IDs (D415/D435/D435i/D455/...)
realsense_ids="8086:0b3a 8086:0b07 8086:0b64 8086:0b68 8086:0ad3 8086:0ad4"
realsense_found=()
if command -v lsusb >/dev/null; then
  for id in ${realsense_ids}; do
    if lsusb -d "${id}" 2>/dev/null | grep -q "${id}"; then
      realsense_found+=("${id}")
    fi
  done
fi

# V4L2 devices
v4l_devices=()
if command -v v4l2-ctl >/dev/null; then
  while IFS= read -r line; do
    if echo "${line}" | grep -qE '^\s*/dev/video'; then
      v4l_devices+=("$(echo "${line}" | tr -d '\t ')")
    fi
  done < <(v4l2-ctl --list-devices 2>/dev/null || true)
fi
[ ${#v4l_devices[@]} -eq 0 ] && {
  # fallback: glob
  for d in /dev/video*; do
    [ -e "${d}" ] && v4l_devices+=("${d}")
  done
}

# Janus — parse "Janus version: 1400 (1.4.0)" -> "1.4.0"
janus_version=""
janus_path=""
parse_janus_version() {
  local raw="$1"
  echo "${raw}" | head -1 | grep -oE '\([0-9]+\.[0-9]+\.[0-9]+\)' | tr -d '()' | head -1
}
if command -v janus >/dev/null; then
  janus_path="$(command -v janus)"
  janus_version="$(parse_janus_version "$(janus --version 2>&1)")"
elif [ -x /opt/janus/bin/janus ]; then
  janus_path="/opt/janus/bin/janus"
  janus_version="$(parse_janus_version "$(/opt/janus/bin/janus --version 2>&1)")"
fi
[ -z "${janus_version}" ] && janus_version=""   # ensure clean empty

# ffmpeg — first line "ffmpeg version N.M.K-blah" -> "N.M.K-blah"
ffmpeg_version=""
if command -v ffmpeg >/dev/null; then
  ffmpeg_version="$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}' | tr -d '\n')"
fi

# coturn — prefer dpkg (apt-installed), fall back to binary probe
coturn_version=""
if command -v turnserver >/dev/null; then
  if command -v dpkg-query >/dev/null; then
    coturn_version="$(dpkg-query -W -f='${Version}' coturn 2>/dev/null | tr -d '\n')"
  fi
  [ -z "${coturn_version}" ] && coturn_version="installed"
fi

# Python
python_version=""
if command -v python3.12 >/dev/null; then
  python_version="$(python3.12 --version 2>&1 | awk '{print $2}')"
elif command -v python3 >/dev/null; then
  python_version="$(python3 --version 2>&1 | awk '{print $2}')"
fi

# pyrealsense2 installed? Check candidate venvs + system python in order.
pyrealsense_installed=false
pyrealsense_path=""
for py in \
    /opt/janus-camera-page/venv/bin/python \
    /opt/janus-camera-page/.venv/bin/python \
    "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
    python3; do
  if command -v "${py}" >/dev/null 2>&1; then
    if pypath="$("${py}" -c 'import pyrealsense2 as rs; print(rs.__file__)' 2>/dev/null)" && [ -n "${pypath}" ]; then
      pyrealsense_installed=true
      pyrealsense_path="${pypath}"
      break
    fi
  fi
done

# Stack services state
stack_camera_page=""
stack_janus=""
stack_coturn=""
if command -v systemctl >/dev/null; then
  stack_camera_page="$(systemctl is-active janus-camera-page 2>/dev/null || echo absent)"
  stack_janus="$(systemctl is-active janus 2>/dev/null || echo absent)"
  stack_coturn="$(systemctl is-active coturn 2>/dev/null || echo absent)"
fi

# Network
primary_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"

# Tier classification
tier=3
case "${os_id}:${os_version}" in
  ubuntu:22.04|ubuntu:24.04) tier=1 ;;
  debian:12) tier=1 ;;
  ubuntu:*|debian:*) tier=2 ;;
esac

# ── Output ────────────────────────────────────────────────────────────
if [ "${JSON_OUT}" = "1" ]; then
  # Build JSON without jq (keep zero-dep)
  printf '{\n'
  printf '  "os": {"id": "%s", "version": "%s", "pretty": "%s"},\n' \
    "${os_id}" "${os_version}" "${os_pretty}"
  printf '  "arch": "%s",\n' "${arch}"
  printf '  "kernel": "%s",\n' "${kernel}"
  printf '  "hostname": "%s",\n' "${hostname}"
  printf '  "is_raspberry_pi": %s,\n' "${is_rpi}"
  printf '  "rpi_model": "%s",\n' "${rpi_model}"
  printf '  "compat_tier": %d,\n' "${tier}"
  printf '  "cameras": {\n'
  printf '    "realsense": ['
  for i in "${!realsense_found[@]}"; do
    [ "${i}" -gt 0 ] && printf ', '
    printf '"%s"' "${realsense_found[${i}]}"
  done
  printf '],\n'
  printf '    "v4l2": ['
  for i in "${!v4l_devices[@]}"; do
    [ "${i}" -gt 0 ] && printf ', '
    printf '"%s"' "${v4l_devices[${i}]}"
  done
  printf ']\n'
  printf '  },\n'
  printf '  "tooling": {\n'
  printf '    "janus": {"installed": %s, "version": "%s", "path": "%s"},\n' \
    "$([ -n "${janus_version}" ] && echo true || echo false)" "${janus_version}" "${janus_path}"
  printf '    "ffmpeg": {"installed": %s, "version": "%s"},\n' \
    "$([ -n "${ffmpeg_version}" ] && echo true || echo false)" "${ffmpeg_version}"
  printf '    "coturn": {"installed": %s, "version": "%s"},\n' \
    "$([ -n "${coturn_version}" ] && echo true || echo false)" "${coturn_version}"
  printf '    "python": {"version": "%s"},\n' "${python_version}"
  printf '    "pyrealsense2": {"installed": %s}\n' "${pyrealsense_installed}"
  printf '  },\n'
  printf '  "stack": {"camera_page": "%s", "janus": "%s", "coturn": "%s"},\n' \
    "${stack_camera_page}" "${stack_janus}" "${stack_coturn}"
  printf '  "network": {"primary_ip": "%s"}\n' "${primary_ip}"
  printf '}\n'
  exit 0
fi

# Human-readable
printf '\n%s\n' "── Environment probe: ${hostname} ─────────────────────────────"
printf 'OS:           %s (%s)\n' "${os_pretty}" "${arch}"
printf 'Kernel:       %s\n' "${kernel}"
if [ "${is_rpi}" = "true" ]; then
  printf 'Hardware:     %s\n' "${rpi_model}"
fi
printf 'Compat tier:  %d  (1=full support, 2=manual pyrealsense, 3=untested)\n' "${tier}"

printf '\nCameras:\n'
if [ ${#realsense_found[@]} -gt 0 ]; then
  printf '  RealSense:  %d device(s): %s\n' "${#realsense_found[@]}" "${realsense_found[*]}"
else
  printf '  RealSense:  none\n'
fi
if [ ${#v4l_devices[@]} -gt 0 ]; then
  printf '  V4L2:       %s\n' "${v4l_devices[*]}"
  if [ "${VERBOSE}" = "1" ] && command -v v4l2-ctl >/dev/null; then
    for d in "${v4l_devices[@]}"; do
      formats="$(v4l2-ctl -d "${d}" --list-formats 2>/dev/null | grep -E "'.+'" | head -3 | tr '\n' ',' || true)"
      [ -n "${formats}" ] && printf '              %s: %s\n' "${d}" "${formats%,}"
    done
  fi
else
  printf '  V4L2:       none\n'
fi

printf '\nTooling:\n'
printf '  Janus:        %s\n' "${janus_version:+installed ${janus_version}}"
printf '  ffmpeg:       %s\n' "${ffmpeg_version:+installed ${ffmpeg_version}}"
printf '  coturn:       %s\n' "${coturn_version:+installed ${coturn_version}}"
printf '  Python:       %s\n' "${python_version:-not found}"
if [ "${pyrealsense_installed}" = "true" ]; then
  printf '  pyrealsense2: installed (%s)\n' "${pyrealsense_path}"
else
  printf '  pyrealsense2: not installed\n'
fi

printf '\nStack services:\n'
printf '  janus-camera-page: %s\n' "${stack_camera_page}"
printf '  janus:             %s\n' "${stack_janus}"
printf '  coturn:            %s\n' "${stack_coturn}"

printf '\nNetwork:\n'
printf '  Primary IP:  %s\n' "${primary_ip}"

# Recommendations
printf '\nRecommendations:\n'
if [ "${tier}" -eq 3 ]; then
  printf '  - OS tier 3 (untested). Recommended: Ubuntu 22.04+/24.04 LTS or Debian 12+.\n'
fi
if [ ${#realsense_found[@]} -eq 0 ] && [ ${#v4l_devices[@]} -eq 0 ]; then
  printf '  - No camera detected. Plug in V4L2 webcam OR RealSense before install.\n'
fi
if [ -z "${janus_version}" ]; then
  printf '  - Janus not installed. Installer will run "apt install janus" (Ubuntu 22.04+).\n'
fi
if [ -z "${ffmpeg_version}" ]; then
  printf '  - ffmpeg not installed. Installer will install via apt.\n'
fi
if [ ${#realsense_found[@]} -gt 0 ] && [ "${pyrealsense_installed}" = "false" ]; then
  if [ "${tier}" -eq 1 ]; then
    printf '  - RealSense attached but pyrealsense2 missing. Installer will use vendored wheel OR PyPI.\n'
  else
    printf '  - RealSense attached. pyrealsense2 will need manual build on tier-%d OS.\n' "${tier}"
  fi
fi
printf '  - To install: sudo ../install.sh\n'
printf '\n'
