#!/usr/bin/env bash
# Lint только camera_bringup (НЕ распространяется на соседние сервисы).
# Используется в pre-commit + CI.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BRINGUP="$(dirname "$HERE")"

cd "$BRINGUP"
exec "$BRINGUP/.venv/bin/python" -m ruff check \
    --config "$BRINGUP/pyproject.toml" "$@" .
