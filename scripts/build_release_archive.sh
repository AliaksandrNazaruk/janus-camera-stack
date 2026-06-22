#!/usr/bin/env bash
# Build a release tarball of the camera-stack project with HARD secret excludes.
#
# WHY: the 2026-06-20 incident proved a gitignored host_infra/secrets.yml was
# FOLLOWED into a hand-built tar — ".gitignore protects git, not release artifacts".
# This script makes the exclude list explicit (scripts/release_excludes.txt, the
# single source of truth) and FAILS CLOSED: if any real secret slips into the
# archive, the build aborts and deletes the bad artifact.
#
# Usage:
#   build_release_archive.sh [SRC_DIR] [OUT_TGZ]
#     SRC_DIR  project dir to package   (default: the service root = scripts/..)
#     OUT_TGZ  output path              (default: <SRC>/_archive/<base>_release_<ts>.tar.gz)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXCLUDES="$SCRIPT_DIR/release_excludes.txt"
[ -f "$EXCLUDES" ] || { echo "FATAL: missing exclude list: $EXCLUDES" >&2; exit 2; }

SRC_DIR="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC_DIR="$(cd "$SRC_DIR" && pwd)"
SRC_PARENT="$(dirname "$SRC_DIR")"
SRC_BASE="$(basename "$SRC_DIR")"

OUT_TGZ="${2:-$SRC_DIR/_archive/${SRC_BASE}_release_$(date +%Y%m%d_%H%M%S).tar.gz}"
mkdir -p "$(dirname "$OUT_TGZ")"

# Build (exclude the output dir too, so we never nest archives).
tar czf "$OUT_TGZ" \
    --exclude-from="$EXCLUDES" \
    --exclude="$SRC_BASE/_archive" \
    -C "$SRC_PARENT" "$SRC_BASE"

# Verify — fail CLOSED if any secret-bearing entry made it in (belt to the
# exclude list's suspenders; also catches a gutted exclude file). Capture the
# listing ONCE; matching via here-string/case avoids the pipefail+grep -q SIGPIPE
# hazard that would otherwise make tar report failure on an early grep exit.
listing="$(tar tzf "$OUT_TGZ")"
# Belt to the exclude list's suspenders. Secrets (the original incident) PLUS non-shippable
# hygiene artifacts (A1: a stray nested review tarball + local dev settings got bundled).
bad="$(grep -iE '(^|/)host_infra/secrets\.yml$|(^|/)camera-secrets\.env$|\.pre-rotate-|\.(pem|key|token|tar|tgz)$|\.tar\.gz$|(^|/)\.claude/|\.local\.json$' <<<"$listing" \
        | grep -vE '\.(example|sample)$' || true)"
if [ -n "$bad" ]; then
    echo "FATAL: secret-bearing or non-shippable entries present in archive:" >&2
    echo "$bad" | sed 's/^/  /' >&2
    rm -f "$OUT_TGZ"
    echo "Aborted: removed bad archive (fail-closed)." >&2
    exit 1
fi

# Sanity: the shippable placeholder SHOULD be present (catches over-exclusion).
case "$listing" in
    *secrets.yml.example*) : ;;
    *) echo "WARN: secrets.yml.example not found in archive (over-excluded?)" >&2 ;;
esac

SHA="$(sha256sum "$OUT_TGZ" | awk '{print $1}')"
N="$(tar tzf "$OUT_TGZ" | wc -l)"
echo "OK: release archive built (secret-free, verified)"
echo "  path:    $OUT_TGZ"
echo "  entries: $N"
echo "  sha256:  $SHA"
