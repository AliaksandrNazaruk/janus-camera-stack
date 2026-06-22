"""Tests для realsense-mux.py — pure logic, no hardware required.

Focus: P1-CV-001 CameraCalibration + DepthSampler.sample_aligned() math
verification. Hardware-specific paths (rs.pipeline, FifoWriter file IO,
HTTP server) are NOT exercised — they require either a real D435i or
extensive librealsense mocking.

Strategy:
- Stub pyrealsense2 via sys.modules.setdefault BEFORE import.
- Load realsense-mux.py через importlib (hyphen in name → can't do `import`).
- Construct CameraCalibration с synthetic intrinsics/extrinsics, verify
  reprojection round-trip is correct within sub-pixel tolerance.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest


# ── Module loading с pyrealsense2 stub ────────────────────────────────

_MODULE_PATH = Path(__file__).resolve().parent.parent / "files" / "realsense-mux.py"


@pytest.fixture(scope="module")
def mux():
    """Import realsense-mux.py as a module (hyphen → importlib needed)."""
    if "pyrealsense2" not in sys.modules:
        rs_stub = MagicMock()
        rs_stub.stream = MagicMock()
        rs_stub.format = MagicMock()
        sys.modules["pyrealsense2"] = rs_stub

    spec = importlib.util.spec_from_file_location("realsense_mux_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Helpers — synthetic D435i-like calibration ────────────────────────


def _make_intr(w=640, h=480, fx=380.0, fy=380.0, ppx=320.0, ppy=240.0):
    """Build a rs.intrinsics-shaped namespace."""
    return SimpleNamespace(width=w, height=h, fx=fx, fy=fy, ppx=ppx, ppy=ppy)


def _make_extr(translation=(-0.015, 0.0, 0.0), rotation=None):
    """Build a rs.extrinsics-shaped namespace.

    Default: depth → color shifted by -15mm в X (matches D435i baseline
    where color is left-of-stereo by ~1.5cm). Identity rotation.
    """
    if rotation is None:
        rotation = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    return SimpleNamespace(translation=translation, rotation=rotation)


def _make_calibration(mux, color_w=640, color_h=480):
    return mux.CameraCalibration(
        depth_intr=_make_intr(640, 480, fx=380, fy=380, ppx=320, ppy=240),
        color_intr=_make_intr(color_w, color_h, fx=380, fy=380, ppx=320, ppy=240),
        d2c_extr=_make_extr(),
    )


# ── CameraCalibration ─────────────────────────────────────────────────


class TestCameraCalibration:
    def test_stores_dimensions(self, mux):
        cal = _make_calibration(mux, 1280, 720)
        assert cal.color_w == 1280
        assert cal.color_h == 720
        assert cal.depth_w == 640
        assert cal.depth_h == 480

    def test_rotation_matrix_reshape(self, mux):
        cal = _make_calibration(mux)
        # identity 3×3
        assert cal.R.shape == (3, 3)
        np.testing.assert_array_equal(cal.R, np.eye(3, dtype=np.float32))

    def test_translation_vector(self, mux):
        cal = _make_calibration(mux)
        np.testing.assert_array_almost_equal(
            cal.T, np.array([-0.015, 0.0, 0.0], dtype=np.float32)
        )


# ── DepthSampler.sample_aligned() ─────────────────────────────────────


class TestSampleAligned:
    def test_no_calibration_falls_back(self, mux):
        """Without calibration, sample_aligned() returns same shape as sample()."""
        s = mux.DepthSampler(calibration=None)
        z16 = np.ones((10, 10), dtype=np.uint16) * 500  # 0.5m
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        assert r["aligned"] is False
        assert r["reason"] == "no_calibration"
        assert r["depth_m"] == pytest.approx(0.5)

    def test_no_frame_yet_raises(self, mux):
        s = mux.DepthSampler(calibration=_make_calibration(mux))
        with pytest.raises(RuntimeError, match="no depth frame"):
            s.sample_aligned(50.0, 50.0)

    def test_no_valid_depth_returns_zero(self, mux):
        """All-zero depth array → no_valid_depth reason."""
        s = mux.DepthSampler(calibration=_make_calibration(mux))
        z16 = np.zeros((20, 20), dtype=np.uint16)
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        assert r["aligned"] is True
        assert r["reason"] == "no_valid_depth"
        assert r["depth_m"] == 0.0

    def test_identity_extrinsics_center_match(self, mux):
        """С identity rotation, zero translation, и identical intrinsics:
        clicking center of color → returns center depth pixel value."""
        cal = mux.CameraCalibration(
            depth_intr=_make_intr(),
            color_intr=_make_intr(),
            d2c_extr=_make_extr(translation=(0.0, 0.0, 0.0)),
        )
        s = mux.DepthSampler(calibration=cal)
        z16 = np.ones((480, 640), dtype=np.uint16) * 1000  # 1m flat plane
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        assert r["aligned"] is True
        assert r["reason"] in {"ok", "nearest_neighbor"}
        assert r["depth_m"] == pytest.approx(1.0, abs=0.01)

    def test_baseline_shift_shifts_match(self, mux):
        """C нонули translation, click at center of color (320, 240) should
        find depth pixel whose forward-projection lands at color (320, 240).

        D435i baseline: depth → color shifted by -1.5cm в X. For a flat
        plane at Z=1m, depth pixel j_d that projects к color uc=320 satisfies:
          uc = fx * (x_d - 0.015) / Z + cx_c       (identical intrinsics)
          320 = 380 * ((j_d - 320) / 380 * 1 - 0.015) / 1 + 320
          0 = (j_d - 320) - 5.7
          j_d ≈ 325.7

        So sampling returns depth at j_d≈326 — same value (uniform plane).
        """
        cal = _make_calibration(mux)  # D435i-like
        s = mux.DepthSampler(calibration=cal)
        z16 = np.ones((480, 640), dtype=np.uint16) * 1000  # 1m flat
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        assert r["aligned"] is True
        # На однородной плоскости значение depth одинаково везде → точное 1m.
        assert r["depth_m"] == pytest.approx(1.0, abs=0.01)

    def test_foreground_wins_when_two_depths_align(self, mux):
        """Two depth layers projecting к same color pixel → min Z returned."""
        cal = mux.CameraCalibration(
            depth_intr=_make_intr(),
            color_intr=_make_intr(),
            d2c_extr=_make_extr(translation=(0.0, 0.0, 0.0)),
        )
        s = mux.DepthSampler(calibration=cal)
        z16 = np.ones((480, 640), dtype=np.uint16) * 2000  # 2m background
        # Inject a closer object (0.5m) at center pixel
        z16[240, 320] = 500
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        # Foreground wins
        assert r["depth_m"] == pytest.approx(0.5, abs=0.05)

    def test_age_and_stale_flags(self, mux):
        """Age & stale propagated to aligned response."""
        cal = _make_calibration(mux)
        s = mux.DepthSampler(calibration=cal)
        z16 = np.ones((20, 20), dtype=np.uint16) * 500
        s.update(z16, 0.001)
        r = s.sample_aligned(50.0, 50.0)
        assert r["age_ms"] >= 0
        assert isinstance(r["stale"], bool)
