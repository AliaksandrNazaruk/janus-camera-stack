#!/usr/bin/env bash
# Run pip-audit against L0 venv. Exits non-zero if vulnerabilities found.
# Run in CI или периодически (раз в неделю).
#
# Exits:
#   0 = no vulnerabilities
#   1 = vulnerabilities found
#   2 = pip-audit not installed
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BRINGUP="$(dirname "$HERE")"
VENV="$BRINGUP/.venv"
PIP_AUDIT="$VENV/bin/pip-audit"

if [ ! -x "$PIP_AUDIT" ]; then
    echo "[audit] pip-audit not installed in L0 venv"
    echo "        install: $VENV/bin/pip install pip-audit"
    exit 2
fi

echo "[audit] Scanning $VENV for known vulnerabilities..."
"$PIP_AUDIT" --strict --format json --output "$BRINGUP/audit-report.json" || EXIT_CODE=$?

if [ "${EXIT_CODE:-0}" = "0" ]; then
    echo "[audit] No vulnerabilities found"
    exit 0
else
    echo "[audit] FOUND vulnerabilities — see audit-report.json"
    "$PIP_AUDIT" --format columns
    exit 1
fi
