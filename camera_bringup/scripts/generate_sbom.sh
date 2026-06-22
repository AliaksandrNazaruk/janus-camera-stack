#!/usr/bin/env bash
# Generate SBOM (Software Bill of Materials) для L0 deps.
#
# Outputs:
#   SBOM.json     — full pip list (CycloneDX-like JSON)
#   SBOM.txt      — human-readable, only L0 direct + first-level transitive
#
# Run periodically (weekly via cron) или в CI.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BRINGUP="$(dirname "$HERE")"
VENV_PIP="$BRINGUP/.venv/bin/pip"
SBOM_JSON="$BRINGUP/SBOM.json"
SBOM_TXT="$BRINGUP/SBOM.txt"

echo "[sbom] Generating from $VENV_PIP"

# Full structured SBOM (machine-readable)
"$VENV_PIP" list --format=json > "$SBOM_JSON"

# Human-readable: только наши direct deps + первого уровня transitive
{
    echo "# L0 Software Bill of Materials"
    echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# Source: camera_bringup/.venv/"
    echo ""
    echo "## Direct dependencies (from requirements.txt)"
    grep -E "^[a-zA-Z]" "$BRINGUP/requirements.txt" | awk -F'==' '{print "- " $1 " " $2}' | tr -d '\\'
    echo ""
    echo "## Full venv contents (`pip list`)"
    "$VENV_PIP" list --format=columns
} > "$SBOM_TXT"

echo "[sbom] Written:"
echo "  $SBOM_JSON ($(wc -c < "$SBOM_JSON") bytes)"
echo "  $SBOM_TXT  ($(wc -l < "$SBOM_TXT") lines)"
