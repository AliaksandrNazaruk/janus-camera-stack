"""Boundary fitness tests — enforce layer contracts via static analysis.

CONTRACT.md claims L4 calls L3/L2 via admin CLIs (janus-admin /
encoder-admin). Not via raw `sudo systemctl`. Without enforcement this is just
aspiration — anyone may add a raw systemctl call and tests pass.

These tests grep production code (app/) for violations. Test files and
infrastructure scripts excluded.

Allowed exceptions: NONE — P1 closed the last one. The FDIR reboot now goes through the scoped
service-admin CLI (recovery_executor → `sudo -n /usr/local/bin/service-admin reboot`).

Recently resolved (no longer needs exception):
  - recovery_executor.py `sudo systemctl reboot` → now via `service-admin reboot` (P1 boundary)
  - realsense-failsafe.service direct call → now via `camera-admin reset-usb`
  - v4l2-ctl direct invocations → now via `camera-admin v4l2-*`
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_APP_ROOT = Path(__file__).resolve().parent.parent / "app"


def _production_py_files() -> list[Path]:
    """Production Python files only — no tests, no __pycache__."""
    return [
        p for p in _APP_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


# ── Approved exceptions (must match comment in CONTRACT.md) ────────────

# EMPTIED by P1: the FDIR reboot now goes through the scoped service-admin CLI (recovery_executor →
# `sudo -n /usr/local/bin/service-admin reboot`), so there is no longer a raw `sudo systemctl` call in
# production. No approved leaks remain.
_APPROVED_LEAKS: set[tuple[str, str, str]] = set()


def _is_approved_leak(file_path: Path, line: str) -> bool:
    """Check if a raw systemctl line matches a documented approved exception."""
    for (suffix, allowed_pattern, _why) in _APPROVED_LEAKS:
        if str(file_path).endswith(suffix) and allowed_pattern in line:
            return True
    return False


# ── Fitness tests ──────────────────────────────────────────────────────

def test_no_raw_sudo_systemctl_in_production():
    """L4 production code MUST call L2/L3 via admin CLIs, not direct systemctl.

    Detects ACTUAL subprocess invocations (list form with adjacent "sudo",
    "systemctl" string items + comma separator). Does not trigger on prose in
    docstrings / comments that mentions the old behavior.
    """
    # Strict pattern: "sudo" followed by "," followed by "systemctl" — only
    # matches subprocess argument lists, not free text.
    sudo_systemctl_pattern = re.compile(r'["\']sudo["\']\s*,\s*["\']systemctl["\']')

    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            # Skip comments + docstring lines (rough heuristic)
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue

            if sudo_systemctl_pattern.search(line):
                if _is_approved_leak(f, line):
                    continue
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(
            f"  {f}:{lineno}: {line}" for f, lineno, line in violations
        )
        pytest.fail(
            f"\n{len(violations)} raw 'sudo systemctl' calls found in production code.\n"
            f"L4 MUST call admin CLIs (janus-admin / encoder-admin) per CONTRACT.md.\n"
            f"Add approved exception to _APPROVED_LEAKS if this is intentional.\n\n"
            f"Violations:\n{details}"
        )


def test_no_direct_jcfg_writes_in_production():
    """L4 MUST NOT write /opt/janus/etc/janus/*.jcfg files directly.

    Writes go through janus-admin CLI (single owner pattern + flock).
    Reads OK — but writes (open(..., 'w') / write_text() targeting that path)
    indicate layer boundary violation.
    """
    jcfg_path_pattern = re.compile(r'/opt/janus/etc/janus/[\w.-]+\.jcfg')
    write_indicators = ("open(", "write_text(", "atomic_write_text(", "atomic_write(")

    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        # nat_config.py now delegates to janus-admin — OK to ignore
        # if it contains only a subprocess.run invocation.
        text = f.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not jcfg_path_pattern.search(line):
                continue
            # Check for write indicators on same or adjacent line
            if any(ind in line for ind in write_indicators):
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(
            f"  {f}:{lineno}: {line}" for f, lineno, line in violations
        )
        pytest.fail(
            f"\n{len(violations)} direct jcfg writes found in production code.\n"
            f"L4 MUST go through janus-admin CLI for jcfg mutations.\n\n"
            f"Violations:\n{details}"
        )


def test_approved_leaks_still_documented_in_contract():
    """Sanity: every _APPROVED_LEAKS entry must be documented in CONTRACT.md.

    Prevents silent acceptance of leaks. If a pattern in _APPROVED_LEAKS is not
    mentioned in CONTRACT.md known issues — fix CONTRACT.md OR remove the leak.
    """
    contract = _APP_ROOT.parent / "docs" / "CONTRACT.md"
    assert contract.exists(), "L4 CONTRACT.md missing"
    contract_text = contract.read_text().lower()

    missing: list[tuple[str, str]] = []
    for (suffix, pattern, _why) in _APPROVED_LEAKS:
        # Pattern must be mentioned (case-insensitive)
        if pattern.lower() not in contract_text:
            missing.append((suffix, pattern))

    if missing:
        details = "\n".join(f"  {p} (in {s})" for s, p in missing)
        pytest.fail(
            f"\nApproved leaks NOT mentioned in janus_camera_page/CONTRACT.md:\n{details}\n"
            f"Either document them in 'Known cross-layer leaks' OR remove from _APPROVED_LEAKS."
        )


def test_no_direct_janus_apply_config_invocation():
    """Deprecated /usr/local/sbin/janus-apply-config.sh — path-injection vuln.

    Removed by host_infra security cleanup. Any L4 code calling it = bug.
    """
    pattern = re.compile(r'janus-apply-config')
    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if pattern.search(line):
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, line.strip()))

    if violations:
        details = "\n".join(
            f"  {f}:{lineno}: {line}" for f, lineno, line in violations
        )
        pytest.fail(f"\njanus-apply-config.sh callers found (deprecated):\n{details}")


def test_no_direct_v4l2_ctl_invocation():
    """L4 MUST call v4l2 operations via the L0-owned camera-admin CLI.

    Direct v4l2-ctl subprocess calls are considered a boundary leak — L4
    knows the specific tool name + flags. Allowed: comments + docstrings
    that reference it descriptively. Production code MUST use camera-admin.
    """
    # Strict pattern: subprocess list-form "v4l2-ctl" string item, not
    # arbitrary mention. Matches ["v4l2-ctl", ...] but not "use v4l2-ctl".
    pattern = re.compile(r'["\']v4l2-ctl["\']\s*,')
    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if pattern.search(line):
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(
            f"  {f}:{lineno}: {line}" for f, lineno, line in violations
        )
        pytest.fail(
            f"\nDirect v4l2-ctl invocations found in production code.\n"
            f"L4 MUST call camera-admin CLI (sudo /usr/local/bin/camera-admin v4l2-*).\n\n"
            f"Violations:\n{details}"
        )
