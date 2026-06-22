#!/usr/bin/env bash
# Fetch the official janus.js browser client (MIT, Meetecho) into templates/.
# Pinned to the Janus version this stack builds (deploy/janus/Dockerfile).
# The file is vendored in the repo; run this only to refresh / bump the version.
set -euo pipefail
JANUS_VERSION="${JANUS_VERSION:-v1.2.4}"
URL="https://raw.githubusercontent.com/meetecho/janus-gateway/${JANUS_VERSION}/html/demos/janus.js"
DEST="$(cd "$(dirname "$0")/.." && pwd)/templates/janus.js"
echo "Fetching janus.js ${JANUS_VERSION} → ${DEST}"
curl -fsSL "$URL" -o "$DEST"
grep -q "The MIT License" "$DEST" || { echo "ERROR: MIT header missing — refusing"; exit 1; }
echo "OK ($(wc -c <"$DEST") bytes, MIT header present)"
