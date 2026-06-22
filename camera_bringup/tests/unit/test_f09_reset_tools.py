"""Unit tests для f09_reset_tools fixer.

Guards regression: hardware path MUST install pyrealsense2 via
requirements-hardware.txt (split в v3.2.1, fixer wiring fix в v3.2.2).
Без этого guard, future change splits requirements again и forgets
to update fixer → silent hardware-deploy breakage.
"""
from __future__ import annotations

from camera_bringup.fixers.f09_reset_tools import ResetToolsFixer


def test_plan_includes_pure_python_requirements_install():
    """Plan must reference requirements.txt (pure-Python deps)."""
    fixer = ResetToolsFixer()
    actions = fixer.plan(ctx={})
    payloads = [a.payload or "" for a in actions]
    assert any("requirements.txt" in p and "requirements-hardware.txt" not in p
               for p in payloads), \
        f"Plan must include pure-Python requirements.txt install. Got: {payloads}"


def test_plan_includes_hardware_requirements_install():
    """Plan must reference requirements-hardware.txt (pyrealsense2).

    Regression guard: split в v3.2.1 unwittingly broke hardware path until
    fixer was updated в v3.2.2. Without this assertion, future requirement
    refactoring could silently break pyrealsense2 install on hardware nodes.
    """
    fixer = ResetToolsFixer()
    actions = fixer.plan(ctx={})
    payloads = [a.payload or "" for a in actions]
    assert any("requirements-hardware.txt" in p for p in payloads), \
        f"Plan must include hardware requirements install. Got: {payloads}"


def test_hardware_install_uses_require_hashes():
    """Supply chain integrity: hardware install MUST use --require-hashes."""
    fixer = ResetToolsFixer()
    actions = fixer.plan(ctx={})
    hardware_actions = [
        a for a in actions if "requirements-hardware.txt" in (a.payload or "")
    ]
    assert hardware_actions, "no hardware install action found"
    for a in hardware_actions:
        assert "--require-hashes" in (a.payload or ""), \
            f"Hardware install MUST use --require-hashes (NIST SP 800-218): {a.payload}"


def test_plan_includes_chmod_exec_on_script():
    """Plan must chmod +x hw_reset_realsense.py script."""
    fixer = ResetToolsFixer()
    actions = fixer.plan(ctx={})
    chmod_actions = [a for a in actions if a.kind == "chmod_exec"]
    assert chmod_actions, "no chmod_exec action found"
    assert any("hw_reset_realsense" in str(a.target) for a in chmod_actions)


def test_plan_order_venv_first_then_install_then_chmod():
    """Action order: venv create (if needed) → pure install → hardware install → chmod."""
    fixer = ResetToolsFixer()
    actions = fixer.plan(ctx={})
    # Skip optional venv create action
    install_pure_idx = next(
        (i for i, a in enumerate(actions)
         if "requirements.txt" in (a.payload or "")
         and "requirements-hardware" not in (a.payload or "")),
        None,
    )
    install_hw_idx = next(
        (i for i, a in enumerate(actions)
         if "requirements-hardware.txt" in (a.payload or "")),
        None,
    )
    chmod_idx = next((i for i, a in enumerate(actions) if a.kind == "chmod_exec"), None)
    assert install_pure_idx is not None and install_hw_idx is not None and chmod_idx is not None
    assert install_pure_idx < install_hw_idx < chmod_idx, \
        f"order invariant violated: pure={install_pure_idx}, hw={install_hw_idx}, chmod={chmod_idx}"


def test_fixer_does_not_require_root():
    """L0 venv lives в boris-owned path — no root needed."""
    fixer = ResetToolsFixer()
    assert fixer.requires_root is False
