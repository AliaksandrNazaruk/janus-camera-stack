"""Property-based tests с hypothesis.

Generates тысячи случайных inputs для критичных pure functions, находит
edge cases которые example-based tests не покрывают.

Покрывает:
  - L0._derive_status: для любой combination CheckResult statuses → валидный LayerStatus
  - c11._compare: симметрия, рефлексивность, severity priority
  - signing.verify: round-trip property, tamper detection
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from camera_bringup.api import L0, LayerStatus
from camera_bringup.check import CheckResult, Status
from camera_bringup.checks import ALL_CHECKS
from camera_bringup.fixers import ALL_FIXERS

# ── Strategies ────────────────────────────────────────────────────────

_status_strategy = st.sampled_from(list(Status))
_check_names = [name for name, _ in ALL_CHECKS]


@st.composite
def check_result_strategy(draw):
    """Generate валидный CheckResult с random name+status."""
    name = draw(st.sampled_from([*_check_names, "unknown_check", "fake_check"]))
    status = draw(_status_strategy)
    return CheckResult(name=name, status=status, summary="fuzz")


class TestDeriveStatusProperties:
    @given(st.lists(check_result_strategy(), min_size=0, max_size=20))
    @settings(max_examples=500, deadline=None)
    def test_derive_status_always_returns_valid_enum(self, results):
        """Для ЛЮБОЙ combination CheckResult'ов — derive_status возвращает
        валидный LayerStatus enum (никогда не raises, не None)."""
        result = L0._derive_status(results)
        assert isinstance(result, LayerStatus)

    @given(st.lists(check_result_strategy(), min_size=1, max_size=20))
    @settings(max_examples=200, deadline=None)
    def test_error_always_dominates(self, results):
        """Если есть хоть один ERROR — status ВСЕГДА UNKNOWN."""
        # Force first result to ERROR
        results[0] = CheckResult(name=results[0].name, status=Status.ERROR, summary="forced error")
        assert L0._derive_status(results) == LayerStatus.UNKNOWN

    @given(st.lists(check_result_strategy(), min_size=0, max_size=10))
    @settings(max_examples=200, deadline=None)
    def test_all_ok_or_skip_is_healthy(self, results):
        """Если все checks OK или SKIP (no WARN/FAIL/ERROR) — HEALTHY."""
        # Force all to OK or SKIP
        results = [
            CheckResult(name=r.name, status=Status.OK if i % 2 == 0 else Status.SKIP, summary="x")
            for i, r in enumerate(results)
        ]
        assert L0._derive_status(results) == LayerStatus.HEALTHY

    @given(check_result_strategy())
    @settings(max_examples=100, deadline=None)
    def test_single_check_status_consistent(self, result):
        """Одиночный check — derive_status предсказуем по правилам."""
        layer = L0._derive_status([result])
        if result.status == Status.ERROR:
            assert layer == LayerStatus.UNKNOWN
        elif result.status == Status.OK:
            assert layer == LayerStatus.HEALTHY
        elif result.status == Status.SKIP:
            assert layer == LayerStatus.HEALTHY
        elif result.status == Status.FAIL:
            # FAIL → BROKEN или DRIFTED в зависимости от наличия fixer'а
            expected = LayerStatus.DRIFTED if result.name in ALL_FIXERS else LayerStatus.BROKEN
            assert layer == expected
        elif result.status == Status.WARN:
            expected = LayerStatus.DRIFTED if result.name in ALL_FIXERS else LayerStatus.DEGRADED
            assert layer == expected


class TestFingerprintCompareProperties:
    @st.composite
    def _fingerprint_strategy(draw):
        return {
            "camera": {
                "vendor_id": draw(st.text(min_size=1, max_size=10)),
                "product_id": draw(st.text(min_size=1, max_size=10)),
                "serial": draw(st.one_of(st.none(), st.text(min_size=1, max_size=20))),
                "firmware": draw(st.one_of(st.none(), st.text(min_size=1, max_size=20))),
            },
            "host": {
                "sysfs_path": draw(st.one_of(st.none(), st.text(min_size=1, max_size=50))),
            },
        }

    @given(_fingerprint_strategy())
    @settings(max_examples=200, deadline=None)
    def test_identical_inputs_no_diffs(self, fp):
        """Identical fingerprints → no diffs (reflexivity)."""
        from camera_bringup.checks.c11_fingerprint import _compare
        assert _compare(fp, fp) == []

    @given(_fingerprint_strategy(), _fingerprint_strategy())
    @settings(max_examples=200, deadline=None)
    def test_diffs_have_valid_severity(self, current, baseline):
        """Все diffs имеют валидный severity (OK/WARN/FAIL/ERROR/SKIP enum value)."""
        from camera_bringup.checks.c11_fingerprint import _compare
        valid_severities = {s.value for s in Status}
        for d in _compare(current, baseline):
            assert d["severity"] in valid_severities

    @given(_fingerprint_strategy())
    @settings(max_examples=100, deadline=None)
    def test_serial_change_always_fail(self, fp):
        """Изменение serial всегда FAIL severity (никогда WARN)."""
        from camera_bringup.checks.c11_fingerprint import _compare
        # Force base serial to be different and non-None
        baseline = dict(fp)
        baseline["camera"] = dict(fp["camera"])
        baseline["camera"]["serial"] = "BASELINE_SERIAL_123"
        current = dict(fp)
        current["camera"] = dict(fp["camera"])
        current["camera"]["serial"] = "CURRENT_SERIAL_456"

        diffs = _compare(current, baseline)
        serial_diffs = [d for d in diffs if d["field"] == "camera.serial"]
        if serial_diffs:
            assert serial_diffs[0]["severity"] == Status.FAIL.value


class TestSigningProperties:
    @given(
        st.dictionaries(
            st.text(min_size=1, max_size=10),
            st.one_of(st.text(), st.integers(), st.booleans()),
            min_size=1, max_size=10,
        ),
        st.binary(min_size=32, max_size=64),
    )
    @settings(max_examples=200, deadline=None)
    def test_sign_then_verify_succeeds(self, payload, secret):
        """Round-trip: sign(p, s); verify(attach(p, s), s) ⇒ True."""
        from camera_bringup.signing import attach_signature, verify
        signed = attach_signature(payload, secret)
        assert verify(signed, secret) is True

    @given(
        st.dictionaries(st.text(min_size=1, max_size=10), st.text(), min_size=1, max_size=10),
        st.binary(min_size=32, max_size=64),
        st.binary(min_size=32, max_size=64),
    )
    @settings(max_examples=200, deadline=None)
    def test_wrong_secret_always_fails_verify(self, payload, secret1, secret2):
        """Verify с НЕправильным secret ВСЕГДА False (если secrets разные)."""
        from camera_bringup.signing import attach_signature, verify
        if secret1 == secret2:
            return  # skip degenerate case
        signed = attach_signature(payload, secret1)
        assert verify(signed, secret2) is False

    @given(
        st.dictionaries(st.text(min_size=1, max_size=10), st.text(), min_size=2, max_size=10),
        st.binary(min_size=32, max_size=64),
    )
    @settings(max_examples=200, deadline=None)
    def test_tampered_content_always_fails_verify(self, payload, secret):
        """Любое изменение content ломает signature."""
        from camera_bringup.signing import attach_signature, verify
        signed = attach_signature(payload, secret)
        # Tamper: добавляем новое поле
        signed["tampered_field"] = "x"
        assert verify(signed, secret) is False


class TestStreamProfileProperties:
    @given(
        st.text(min_size=3, max_size=10),  # pixel_format
        st.integers(min_value=64, max_value=4096),  # width
        st.integers(min_value=64, max_value=4096),  # height
        st.integers(min_value=1, max_value=120),    # fps
    )
    @settings(max_examples=100, deadline=None)
    def test_encoder_kwargs_always_has_required_fields(self, fmt, w, h, fps):
        """encoder_kwargs() ВСЕГДА возвращает video_size, framerate, input_format."""
        from camera_bringup import StreamProfile
        sp = StreamProfile(
            device_path="/dev/test",
            pixel_format=fmt, width=w, height=h, fps=fps,
        )
        kw = sp.encoder_kwargs()
        assert "video_size" in kw
        assert "framerate" in kw
        assert "input_format" in kw
        # Format consistency
        assert f"{w}x{h}" == kw["video_size"]
        assert kw["framerate"] == fps
        assert kw["input_format"] == fmt.lower()


class TestGuaranteesProperties:
    @given(st.lists(st.booleans(), min_size=12, max_size=12))
    @settings(max_examples=100, deadline=None)
    def test_all_satisfied_only_when_all_true(self, bools):
        """all_satisfied() ⇔ all(values)."""
        from camera_bringup import Guarantees
        # 12 named fields
        names = [
            "CAMERA_PRESENT", "SINGLE_D435I", "USB_POWER_LOCKED_ON",
            "UVCVIDEO_QUIRKS_OK", "UDEV_RULE_INSTALLED", "DEV_CAM_RGB_VALID",
            "V4L2_CAPTURE_READY", "FIRMWARE_NOT_TOO_OLD", "BANDWIDTH_FITS_USB",
            "RESET_TOOLS_AVAILABLE", "ENCODER_PREREQS_OK", "IDENTITY_MATCHES_BASELINE",
        ]
        g = Guarantees(**dict(zip(names, bools, strict=True)))
        assert g.all_satisfied() == all(bools)

    @given(st.lists(st.booleans(), min_size=12, max_size=12))
    @settings(max_examples=100, deadline=None)
    def test_unsatisfied_lists_false_fields(self, bools):
        """unsatisfied() возвращает ровно те поля где False."""
        from camera_bringup import Guarantees
        names = [
            "CAMERA_PRESENT", "SINGLE_D435I", "USB_POWER_LOCKED_ON",
            "UVCVIDEO_QUIRKS_OK", "UDEV_RULE_INSTALLED", "DEV_CAM_RGB_VALID",
            "V4L2_CAPTURE_READY", "FIRMWARE_NOT_TOO_OLD", "BANDWIDTH_FITS_USB",
            "RESET_TOOLS_AVAILABLE", "ENCODER_PREREQS_OK", "IDENTITY_MATCHES_BASELINE",
        ]
        d = dict(zip(names, bools, strict=True))
        g = Guarantees(**d)
        expected = [name for name, v in d.items() if not v]
        assert sorted(g.unsatisfied()) == sorted(expected)
