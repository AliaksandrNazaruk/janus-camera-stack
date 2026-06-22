"""Soak CSV file adapter — list + read scripts/soak_*.csv.
Extracted from admin_dashboard (C-04 Phase 4); behavior verbatim, incl. the basename
whitelist (no path traversal) and the 1 MB head+tail truncation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


class InvalidSoakFilename(Exception):
    """Soak filename failed the basename whitelist (no path traversal). Route maps to 400."""


class SoakFileNotFound(Exception):
    """No such soak file. Route maps to 404."""


SOAK_DIR = Path(__file__).resolve().parents[2] / "scripts"
_NAME_RE = re.compile(r"soak_[A-Za-z0-9_]+\.csv")
MAX_BYTES = 1_048_576


def list_files() -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    if SOAK_DIR.is_dir():
        for p in sorted(SOAK_DIR.glob("soak_*.csv")):
            try:
                stat = p.stat()
                lines = sum(1 for _ in p.open()) - 1  # minus header
                out.append({
                    "name": p.name,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "samples": max(0, lines),
                })
            except OSError:
                continue
    return {"files": out, "dir": str(SOAK_DIR)}


def read_file_bytes(name: str) -> bytes:
    """Read one soak CSV by basename. Raises InvalidSoakFilename (route→400, bad name) /
    SoakFileNotFound (route→404, missing). Caps the response at ~1 MB (header + last 1 MB)."""
    if not _NAME_RE.fullmatch(name):
        raise InvalidSoakFilename("invalid filename")
    p = SOAK_DIR / name
    if not p.is_file():
        raise SoakFileNotFound("not found")
    data = p.read_bytes()
    if len(data) > MAX_BYTES:
        head_end = data.find(b"\n") + 1
        data = data[:head_end] + b"# TRUNCATED -- last 1MB\n" + data[-MAX_BYTES:]
    return data
