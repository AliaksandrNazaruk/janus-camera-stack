#!/usr/bin/env bash
# Build the Gateway Operator Console static assets from the design system into
# static/console/ — CSP-safe (no CDN, no babel-in-browser):
#   • JSX (shell/screens + the inline App from the kit's index.html) is precompiled
#     to plain React.createElement JS via esbuild (no runtime transform).
#   • React / ReactDOM / Lucide are self-hosted under vendor/ (no LAN→CDN dep).
#   • CSS tokens are copied; the Google-Fonts @import is dropped (IBM Plex falls
#     back to the system stack until the woff2 are self-hosted).
# Re-run after updating design_system/. Requires node/npx (esbuild fetched on demand).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DS="$ROOT/design_system"
KIT="$DS/ui_kits/operator-console"
OUT="$ROOT/static/console"
ESBUILD="esbuild@0.21.5"

[ -d "$DS" ] || { echo "design_system/ not found at $DS" >&2; exit 1; }
mkdir -p "$OUT/vendor" "$OUT/tokens"

echo "[console-build] precompile JSX → JS (esbuild, classic React.createElement)"
# shell/screens are the kit's components (unchanged); app.live.jsx is OUR
# backend-wired orchestrator (replaces the kit's demo App that read a mock FLEET).
for f in shell screens; do
  npx --yes "$ESBUILD" "$KIT/$f.jsx" --loader:.jsx=jsx --jsx=transform --format=iife > "$OUT/$f.js"
done
npx --yes "$ESBUILD" "$KIT/app.live.jsx" --loader:.jsx=jsx --jsx=transform --format=iife > "$OUT/app.js"

echo "[console-build] copy compiled component bundle + CSS tokens"
cp "$DS/_ds_bundle.js" "$OUT/ds_bundle.js"
cp "$DS"/tokens/*.css "$OUT/tokens/"
# CSP: drop the Google-Fonts @import; keep the rest of the manifest.
grep -v 'fonts.googleapis.com' "$DS/tokens/fonts.css" > "$OUT/tokens/fonts.css" || true
cp "$DS/styles.css" "$OUT/styles.css"
# mock view-model — Phase-1 render proof; the served page fetches the LIVE
# /api/v1/ui/fleet at runtime (console_boot.js) and only falls back to this.
cp "$KIT/fleet-data.js" "$OUT/fleet-data.mock.js"

# Vendor (React/ReactDOM/Lucide UMD) is COMMITTED under $OUT/vendor with a
# SHA256SUMS manifest. A normal build does NOT fetch anything (review P0-2 —
# reproducible/offline): it verifies the committed bytes against the manifest and
# fails closed on mismatch/absence. To bump a version, run with UPDATE_VENDOR=1
# (explicit, online) which re-fetches and regenerates SHA256SUMS to commit.
REACT_VER=18.3.1
LUCIDE_VER=0.460.0
if [ "${UPDATE_VENDOR:-0}" = "1" ]; then
  echo "[console-build] UPDATE_VENDOR=1 — re-fetching vendor + regenerating SHA256SUMS"
  curl -fsSL "https://cdn.jsdelivr.net/npm/react@${REACT_VER}/umd/react.production.min.js"        -o "$OUT/vendor/react.production.min.js"
  curl -fsSL "https://cdn.jsdelivr.net/npm/react-dom@${REACT_VER}/umd/react-dom.production.min.js" -o "$OUT/vendor/react-dom.production.min.js"
  curl -fsSL "https://cdn.jsdelivr.net/npm/lucide@${LUCIDE_VER}/dist/umd/lucide.min.js"            -o "$OUT/vendor/lucide.min.js"
  ( cd "$OUT/vendor" && sha256sum *.js > SHA256SUMS )
  echo "  regenerated $OUT/vendor/SHA256SUMS — commit it"
fi
echo "[console-build] verify self-hosted vendor against SHA256SUMS (no CDN fetch)"
[ -f "$OUT/vendor/SHA256SUMS" ] || { echo "[console-build] FATAL: $OUT/vendor/SHA256SUMS missing — vendored assets absent (run UPDATE_VENDOR=1 once)" >&2; exit 1; }
( cd "$OUT/vendor" && sha256sum -c SHA256SUMS --quiet ) || { echo "[console-build] FATAL: vendor checksum mismatch — refuse to build" >&2; exit 1; }
echo "  vendor checksums OK"

echo "[console-build] done → $OUT"
ls -la "$OUT" "$OUT/vendor" | sed 's/^/  /'
