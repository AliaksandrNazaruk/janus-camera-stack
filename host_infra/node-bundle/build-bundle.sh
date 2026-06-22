#!/usr/bin/env bash
# Assemble the camera-node-bootstrap bundle (versioned; signed if GPG_KEY set).
#
# Node-only payload pulled from the TESTED encoder role files (no duplication of
# install logic). Emits a versioned, checksummed (and optionally signed) tarball
# the gateway can push over SSH. See DYNAMIC_CAMERA_ONBOARDING.md §7.
#
# Usage:  ./build-bundle.sh [OUT_DIR]            (default: /tmp/camera-node-bundle)
#         GPG_KEY=<id> ./build-bundle.sh         (sign SHA256SUMS — review S8)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
ENC="${REPO_ROOT}/host_infra/roles/encoder/files"
OUT="${1:-/tmp/camera-node-bundle}"
VERSION="$(git -C "${REPO_ROOT}" describe --always --dirty 2>/dev/null || echo nogit)-$(date -u +%Y%m%d%H%M%S)"

log() { printf '[build-bundle] %s\n' "$*" >&2; }

rm -rf "$OUT"
mkdir -p "$OUT/files" "$OUT/probe" "$OUT/wheels" "$OUT/node-agent"

# node-only payload from the tested encoder role files
for f in rs-stream.sh realsense-mux.py rs-stream@.service realsense-mux.service sysctl-realsense-mux.conf; do
  [ -f "${ENC}/${f}" ] || { log "ERROR: missing encoder file ${ENC}/${f}"; exit 1; }
  cp "${ENC}/${f}" "$OUT/files/${f}"
done
cp "${HERE}/bootstrap.sh" "$OUT/bootstrap.sh"
cp "${HERE}/probe/realsense_probe_cli.py" "$OUT/probe/"
cp "${HERE}/node-agent/camera-node-agent.py" "$OUT/node-agent/"
cp "${HERE}/node-agent/camera-node-agent.service" "$OUT/node-agent/"

# offline pyrealsense wheel (arch must match the node == gateway: Pi/arm64)
if ls "${REPO_ROOT}/installer/wheels/"pyrealsense2-*.whl >/dev/null 2>&1; then
  cp "${REPO_ROOT}/installer/wheels/"pyrealsense2-*.whl "$OUT/wheels/"
  log "bundled pyrealsense wheel(s)"
else
  log "WARN: no aarch64 pyrealsense wheel in installer/wheels/ — bundle is NOT fully offline"
  log "      (bootstrap falls back to pip). Build it per host_infra/node-bundle/PYREALSENSE_WHEEL.md."
fi

# version + manifest + checksums
printf 'BUNDLE_VERSION=%s\n' "$VERSION" > "$OUT/VERSION"
( cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
log "assembled ${OUT} (version ${VERSION})"

# authenticity (review S8): unsigned dev build unless GPG_KEY is provided
if [ -n "${GPG_KEY:-}" ]; then
  gpg --default-key "${GPG_KEY}" --detach-sign --armor -o "$OUT/SHA256SUMS.asc" "$OUT/SHA256SUMS"
  log "signed SHA256SUMS with ${GPG_KEY}"
else
  log "WARN: unsigned bundle — set GPG_KEY to sign (required before non-bench use, review S8)"
fi

# tarball
TAR="${OUT}.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$TAR" "$(basename "$OUT")"
sha256sum "$TAR" | tee "${TAR}.sha256" >&2
log "tarball: ${TAR}"
