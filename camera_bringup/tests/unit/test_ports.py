"""Unit-тесты для ports.py (SystemPort) + reference migration c02.

Демонстрирует pattern для будущей migration остальных checks:
без monkeypatch boilerplate — checks принимают SystemPort kwarg, тесты
конструируют FakeSystemPort с in-memory state.
"""
from __future__ import annotations

from camera_bringup.check import Status
from camera_bringup.ports import (
    FakeSystemPort,
    RealSystemPort,
    RunResult,
    SystemPort,
    default_system,
)


class TestRealSystemPort:
    def test_implements_protocol(self):
        assert isinstance(RealSystemPort(), SystemPort)

    def test_read_existing_file(self, tmp_path):
        p = tmp_path / "x"
        p.write_text("hello")
        port = RealSystemPort()
        assert port.read_file(str(p)) == "hello"

    def test_read_missing_returns_none(self):
        port = RealSystemPort()
        assert port.read_file("/nonexistent/path") is None

    def test_exists(self, tmp_path):
        port = RealSystemPort()
        assert port.exists(str(tmp_path))
        assert not port.exists("/no/such/path")

    def test_run_success(self):
        port = RealSystemPort()
        r = port.run(["echo", "hi"])
        assert r.ok
        assert r.stdout.strip() == "hi"

    def test_run_nonexistent_command(self):
        port = RealSystemPort()
        r = port.run(["/nonexistent/binary"])
        assert not r.ok
        assert r.returncode == 127

    def test_run_nonzero_exit(self):
        port = RealSystemPort()
        r = port.run(["false"])
        assert not r.ok
        assert r.returncode == 1


class TestFakeSystemPort:
    def test_implements_protocol(self):
        assert isinstance(FakeSystemPort(), SystemPort)

    def test_read_file_from_dict(self):
        port = FakeSystemPort(files={"/a/b": "content"})
        assert port.read_file("/a/b") == "content"

    def test_read_missing_returns_none(self):
        port = FakeSystemPort()
        assert port.read_file("/nope") is None

    def test_run_records_history(self):
        port = FakeSystemPort()
        port.run(["udevadm", "trigger"])
        port.run(["systemctl", "status"])
        assert port.run_history == [
            ["udevadm", "trigger"],
            ["systemctl", "status"],
        ]

    def test_run_returns_registered_response(self):
        port = FakeSystemPort(
            run_responses={("udevadm", "trigger"): RunResult(returncode=42, stderr="oops")},
        )
        r = port.run(["udevadm", "trigger"])
        assert r.returncode == 42
        assert r.stderr == "oops"

    def test_default_system_returns_real(self):
        assert isinstance(default_system(), RealSystemPort)


class TestC02WithFakeSystemPort:
    """Reference: c02 теперь принимает SystemPort kwarg.
    Демонстрация cleaner тесты без monkeypatch.

    Эти тесты дублируют покрытие test_c02_usb_power.py но без monkeypatch —
    показывают целевой stиль для будущих check migrations.
    """

    def test_canonical_state_via_fake(self):
        from camera_bringup.checks.c02_usb_power import check
        fake = FakeSystemPort(files={
            "/sys/bus/usb/devices/2-2/power/control": "on",
            "/sys/bus/usb/devices/2-2/power/autosuspend": "-1",
            "/sys/bus/usb/devices/2-2/power/persist": "0",
            "/sys/bus/usb/devices/2-2/power/runtime_status": "active",
        })
        result = check(
            {"sysfs_path": "/sys/bus/usb/devices/2-2"},
            system=fake,
        )
        assert result.status == Status.OK

    def test_persist_drift_via_fake(self):
        from camera_bringup.checks.c02_usb_power import check
        fake = FakeSystemPort(files={
            "/sys/bus/usb/devices/2-2/power/control": "on",
            "/sys/bus/usb/devices/2-2/power/autosuspend": "-1",
            "/sys/bus/usb/devices/2-2/power/persist": "1",   # drift
            "/sys/bus/usb/devices/2-2/power/runtime_status": "active",
        })
        result = check(
            {"sysfs_path": "/sys/bus/usb/devices/2-2"},
            system=fake,
        )
        assert result.status == Status.WARN
        assert "persist" in result.summary

    def test_suspended_runtime_via_fake(self):
        from camera_bringup.checks.c02_usb_power import check
        fake = FakeSystemPort(files={
            "/sys/bus/usb/devices/2-2/power/control": "on",
            "/sys/bus/usb/devices/2-2/power/autosuspend": "-1",
            "/sys/bus/usb/devices/2-2/power/persist": "0",
            "/sys/bus/usb/devices/2-2/power/runtime_status": "suspended",
        })
        result = check(
            {"sysfs_path": "/sys/bus/usb/devices/2-2"},
            system=fake,
        )
        assert result.status == Status.FAIL
