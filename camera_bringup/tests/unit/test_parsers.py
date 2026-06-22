"""Unit-тесты для чистых парсеров. Все они принимают строку, возвращают
структуру — без IO, легко покрыть edge cases.
"""
from __future__ import annotations

import pytest


class TestModprobeParser:
    """c03_uvcvideo._parse_modprobe_options."""

    def _parse(self, text: str):
        import os

        # _parse_modprobe_options читает file — даём ему tmp file
        import tempfile

        from camera_bringup.checks.c03_uvcvideo import _parse_modprobe_options
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(text)
            path = f.name
        try:
            return _parse_modprobe_options(path)
        finally:
            os.unlink(path)

    def test_typical_canonical_config(self):
        result = self._parse(
            "options uvcvideo nodrop=1 timeout=500\n"
            "options uvcvideo quirks=128\n"
        )
        assert result == {"nodrop": "1", "timeout": "500", "quirks": "128"}

    def test_comments_ignored(self):
        result = self._parse(
            "# this is comment\n"
            "options uvcvideo nodrop=1\n"
            "# trailing comment\n"
        )
        assert result == {"nodrop": "1"}

    def test_empty_file(self):
        assert self._parse("") == {}

    def test_only_comments(self):
        assert self._parse("# nothing\n# only comments\n") == {}

    def test_non_uvcvideo_options_ignored(self):
        result = self._parse("options some_other_mod foo=bar\n")
        assert result == {}

    def test_multiple_options_same_line(self):
        # позволяем оба формата (один line на key, или multi)
        result = self._parse("options uvcvideo a=1 b=2 c=3\n")
        assert result == {"a": "1", "b": "2", "c": "3"}


class TestNormalizeUdevRule:
    """c04_udev._normalize_rule — для сравнения без шума комментариев."""

    def test_strips_comments_and_blanks(self):
        from camera_bringup.checks.c04_udev import _normalize_rule
        text = (
            "# header\n"
            "\n"
            "SUBSYSTEM==\"video4linux\", SYMLINK+=\"cam-rgb\"\n"
            "  # indented comment\n"
            "\n"
        )
        normalized = _normalize_rule(text)
        assert normalized == 'SUBSYSTEM=="video4linux", SYMLINK+="cam-rgb"'

    def test_preserves_rule_lines_verbatim(self):
        from camera_bringup.checks.c04_udev import _normalize_rule
        # Trailing whitespace strip OK, internal whitespace preserved
        line = "ACTION==\"add|change\", SUBSYSTEM==\"usb\", \\"
        assert _normalize_rule(line) == line

    def test_two_identical_rules_normalize_equal(self):
        from camera_bringup.checks.c04_udev import _normalize_rule
        rule_a = "# v1\nA==\"x\"\n"
        rule_b = "# v2 different comment\nA==\"x\"\n# trailing\n"
        assert _normalize_rule(rule_a) == _normalize_rule(rule_b)


class TestBandwidthMath:
    """c08_bandwidth — pixel format → bytes/px и калькуляция utilization."""

    def test_known_pixel_formats(self):
        from camera_bringup.spec import BYTES_PER_PIXEL
        # YUYV422 = 2 bytes per pixel
        assert BYTES_PER_PIXEL["YUYV"] == 2.0
        # NV12 = 1.5 bytes (Y plane + half-res UV)
        assert BYTES_PER_PIXEL["NV12"] == 1.5
        # RGB3 = 3 bytes
        assert BYTES_PER_PIXEL["RGB3"] == 3.0

    def test_typical_profile_fits_usb2(self):
        # 640x480 YUYV @ 15fps = 73.728 Mbit/s
        # USB2 useful = 360 Mbit → 20%
        from camera_bringup.spec import BYTES_PER_PIXEL, USB2_USEFUL_MBIT
        raw_bitrate = 640 * 480 * 15 * BYTES_PER_PIXEL["YUYV"] * 8 / 1_000_000
        utilization = raw_bitrate / USB2_USEFUL_MBIT * 100
        assert raw_bitrate == pytest.approx(73.728)
        assert utilization < 60  # должны быть в зелёной зоне


class TestShebangParser:
    """c09_reset_tools._shebang_python."""

    def _parse(self, content: str):
        import os
        import tempfile

        from camera_bringup.checks.c09_reset_tools import _shebang_python
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            path = f.name
        try:
            return _shebang_python(path)
        finally:
            os.unlink(path)

    def test_direct_path(self):
        # Direct path to existing interpreter (use /usr/bin/python3 as known-existing)
        result = self._parse("#!/usr/bin/python3\n# rest\n")
        assert result == "/usr/bin/python3"

    def test_env_style(self):
        # env-стиль ищет python3 в PATH
        result = self._parse("#!/usr/bin/env python3\n")
        # должен найти что-то типа /usr/bin/python3
        assert result is not None
        assert "python3" in result

    def test_no_shebang(self):
        result = self._parse("import sys\n")
        assert result is None

    def test_nonexistent_interpreter(self):
        result = self._parse("#!/nonexistent/python\n")
        assert result is None


class TestFirmwareBcdDecode:
    """c07_firmware._decode_bcd."""

    def test_known_format(self):
        from camera_bringup.checks.c07_firmware import _decode_bcd
        # 5100 → "51.00" (BCD pseudo-format)
        assert _decode_bcd("5100") == "51.00"

    def test_short_value(self):
        from camera_bringup.checks.c07_firmware import _decode_bcd
        assert _decode_bcd("12") == "12"

    def test_non_digit(self):
        from camera_bringup.checks.c07_firmware import _decode_bcd
        # non-digit input should not crash, return raw indication
        result = _decode_bcd("abcd")
        assert "raw=abcd" in result
