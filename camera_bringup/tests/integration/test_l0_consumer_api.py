"""Integration tests демонстрирующие L0 API для L1+ consumers.

Эти тесты также служат как **executable documentation** — copy-paste
patterns для разработчика будущего L1/L2/agent.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestTopLevelImports:
    """Public surface — что L1+ должен импортировать."""

    def test_l0_imports_from_package_root(self):
        from camera_bringup import L0
        assert L0 is not None

    def test_layer_status_imports_from_package_root(self):
        from camera_bringup import LayerStatus
        assert LayerStatus.HEALTHY is not None

    def test_typed_dataclasses_importable(self):
        from camera_bringup import (
            CalibrationIntrinsics,
            Identity,
            StreamProfile,
        )
        # All importable from top-level
        assert Identity is not None
        assert CalibrationIntrinsics is not None
        assert StreamProfile is not None

    def test_all_export(self):
        import camera_bringup
        assert "L0" in camera_bringup.__all__
        assert "LayerStatus" in camera_bringup.__all__

    def test_version_set(self):
        import camera_bringup
        assert camera_bringup.__version__


class TestIsReadyConvenience:
    def test_is_ready_returns_bool(self):
        from camera_bringup import L0
        assert isinstance(L0.is_ready(), bool)

    def test_is_usable_returns_bool(self):
        from camera_bringup import L0
        assert isinstance(L0.is_usable(), bool)

    def test_is_usable_implies_status_not_broken_not_safe(self):
        from camera_bringup import L0, LayerStatus
        if L0.is_usable():
            assert L0.status() not in (
                LayerStatus.BROKEN, LayerStatus.UNKNOWN, LayerStatus.SAFE
            )


class TestStreamProfile:
    """Параметры для L2 encoder."""

    def test_stream_profile_returns_typed(self):
        from camera_bringup import L0, StreamProfile
        sp = L0.stream_profile()
        assert isinstance(sp, StreamProfile)

    def test_stream_profile_has_required_fields(self):
        from camera_bringup import L0
        sp = L0.stream_profile()
        assert sp.device_path.startswith("/dev/")
        assert sp.pixel_format
        assert sp.width > 0
        assert sp.height > 0
        assert sp.fps > 0

    def test_stream_profile_encoder_kwargs(self):
        from camera_bringup import L0
        sp = L0.stream_profile()
        kw = sp.encoder_kwargs()
        # ffmpeg-style kwargs
        assert "video_size" in kw
        assert "x" in kw["video_size"]   # "640x480"
        assert kw["framerate"] > 0
        assert kw["input_format"]


class TestCalibration:
    """CV pipeline access."""

    def test_color_calibration(self):
        from camera_bringup import L0, CalibrationIntrinsics
        cal = L0.calibration("color")
        if cal is None:
            pytest.skip("baseline без calibration (legacy)")
        assert isinstance(cal, CalibrationIntrinsics)
        assert cal.fx > 0
        assert cal.fy > 0
        assert cal.width > 0
        assert cal.height > 0
        assert cal.model

    def test_camera_matrix(self):
        from camera_bringup import L0
        cal = L0.calibration("color")
        if cal is None:
            pytest.skip("no calibration")
        K = cal.to_camera_matrix()
        assert len(K) == 3
        assert all(len(row) == 3 for row in K)
        # Структура intrinsics matrix
        assert K[0][0] == cal.fx
        assert K[1][1] == cal.fy
        assert K[0][2] == cal.ppx
        assert K[1][2] == cal.ppy

    def test_available_calibrations_lists_streams(self):
        from camera_bringup import L0
        avail = L0.available_calibrations()
        if not avail:
            pytest.skip("no calibration in baseline")
        # D435i обычно имеет color + depth + infrared
        assert "color" in avail or "depth" in avail


class TestSnapshot:
    def test_snapshot_returns_typed(self):
        from camera_bringup import L0, Snapshot
        s = L0.snapshot()
        assert isinstance(s, Snapshot)

    def test_snapshot_to_dict_json_serializable(self):
        import json

        from camera_bringup import L0
        s = L0.snapshot()
        # Должен сериализоваться
        json.dumps(s.to_dict())

    def test_snapshot_backward_compat_summary(self):
        """L0.summary() = L0.snapshot().to_dict() — старый dict API работает."""
        from camera_bringup import L0
        d = L0.summary()
        assert isinstance(d, dict)
        assert "status" in d
        assert "checks_total" in d


class TestGuaranteesEmphasizesUx:
    def test_attribute_access(self):
        from camera_bringup import L0
        g = L0.guarantees()
        assert isinstance(g.CAMERA_PRESENT, bool)

    def test_unsatisfied_helper(self):
        from camera_bringup import L0
        unsat = L0.guarantees().unsatisfied()
        assert isinstance(unsat, list)

    def test_all_satisfied_helper(self):
        from camera_bringup import L0
        result = L0.guarantees().all_satisfied()
        assert isinstance(result, bool)


class TestRecoveryResultUx:
    def test_needs_attention_property(self):
        from camera_bringup import L0
        r = L0.attempt_recovery(dry_run=True)
        assert isinstance(r.needs_attention, bool)
