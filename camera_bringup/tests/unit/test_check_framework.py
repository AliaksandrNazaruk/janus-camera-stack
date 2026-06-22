"""Unit-тесты для check.py framework: CheckResult, exit_code, safe_run.
"""
from __future__ import annotations

from camera_bringup.check import CheckResult, Status, exit_code, safe_run


class TestCheckResult:
    def test_to_dict_roundtrip(self):
        r = CheckResult(
            name="test",
            status=Status.OK,
            summary="all good",
            details={"key": "val"},
            fix_hint="do X",
        )
        d = r.to_dict()
        assert d["name"] == "test"
        assert d["status"] == "OK"  # enum value, не enum object
        assert d["summary"] == "all good"
        assert d["details"] == {"key": "val"}
        assert d["fix_hint"] == "do X"

    def test_default_details_empty(self):
        r = CheckResult(name="x", status=Status.OK, summary="y")
        assert r.details == {}


class TestExitCode:
    def test_all_ok(self):
        results = [CheckResult(name="a", status=Status.OK, summary="x")]
        assert exit_code(results) == 0

    def test_warn_does_not_fail(self):
        # WARN не должен возвращать exit code != 0 — система работает
        results = [CheckResult(name="a", status=Status.WARN, summary="x")]
        assert exit_code(results) == 0

    def test_fail_returns_1(self):
        results = [
            CheckResult(name="a", status=Status.OK, summary="x"),
            CheckResult(name="b", status=Status.FAIL, summary="y"),
        ]
        assert exit_code(results) == 1

    def test_error_returns_2(self):
        # ERROR (баг в check'е) приоритетнее FAIL
        results = [
            CheckResult(name="a", status=Status.FAIL, summary="x"),
            CheckResult(name="b", status=Status.ERROR, summary="y"),
        ]
        assert exit_code(results) == 2

    def test_empty_results(self):
        # Граничный случай — пустой список = exit 0
        assert exit_code([]) == 0


class TestSafeRun:
    def test_returns_normal_result(self):
        def good_check(ctx):
            return CheckResult(name="ok_check", status=Status.OK, summary="fine")
        result = safe_run("ok_check", good_check, {})
        assert result.status == Status.OK

    def test_exception_becomes_error_result(self):
        def bad_check(ctx):
            raise RuntimeError("boom")
        result = safe_run("bad_check", bad_check, {})
        assert result.status == Status.ERROR
        assert "RuntimeError" in result.summary
        assert "boom" in result.summary
        assert "traceback" in result.details

    def test_name_preserved_in_error(self):
        def bad_check(ctx):
            raise ValueError("nope")
        result = safe_run("my_name", bad_check, {})
        assert result.name == "my_name"
