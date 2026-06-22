"""Базовая инфраструктура для отдельных проверок.

Каждый check возвращает CheckResult. Runner собирает все, печатает
человеко- или машинно-читаемый отчёт.

Семантика статусов:
  OK      — соответствует спеке
  WARN    — отличается, но streaming работает (нужно лечить, не срочно)
  FAIL    — не соответствует и блокирует работу
  SKIP    — нечего проверять (например check'у нужен sudo, а его нет)
  ERROR   — exception в самом check'е (баг проверки, не системы)
"""
from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass
class CheckResult:
    name: str                          # короткий id check'а (snake_case)
    status: Status
    summary: str                       # одна строка для человека
    details: dict[str, Any] = field(default_factory=dict)  # все измерения
    fix_hint: str | None = None     # как починить (если FAIL/WARN)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# Тип функции-чека: принимает пустой context dict (для будущего обмена
# данными между checks), возвращает CheckResult. Никаких side-effects.
CheckFn = Callable[[dict[str, Any]], CheckResult]


def safe_run(name: str, fn: CheckFn, ctx: dict[str, Any]) -> CheckResult:
    """Выполнить check, превратить любое exception в CheckResult(ERROR).
    Логирует результат + duration в structured лог (для observability)."""
    from camera_bringup.logger import get_logger
    log = get_logger("check")
    import time as _t
    t0 = _t.monotonic()
    try:
        result = fn(ctx)
        duration_ms = int((_t.monotonic() - t0) * 1000)
        log.info(
            "check completed",
            extra={
                "check": name,
                "status": result.status.value,
                "summary": result.summary,
                "duration_ms": duration_ms,
            },
        )
        return result
    except Exception as exc:
        duration_ms = int((_t.monotonic() - t0) * 1000)
        tb = traceback.format_exc(limit=4)
        log.error(
            "check raised exception",
            extra={
                "check": name,
                "exception_type": type(exc).__name__,
                "exception_msg": str(exc),
                "duration_ms": duration_ms,
            },
        )
        return CheckResult(
            name=name,
            status=Status.ERROR,
            summary=f"check raised {type(exc).__name__}: {exc}",
            details={"traceback": tb},
        )


# ── Helpers общие для нескольких checks ──────────────────────────────

def read_file(path: str) -> str | None:
    """Прочитать файл; None если не существует или нет прав."""
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, PermissionError):
        return None


def read_int(path: str) -> int | None:
    raw = read_file(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


# ── Печать отчёта ─────────────────────────────────────────────────────

_STATUS_GLYPH = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.SKIP: "·",
    Status.ERROR: "?",
}

# ANSI цвета. Используем только если stdout это tty.
_STATUS_COLOR = {
    Status.OK: "\033[32m",       # green
    Status.WARN: "\033[33m",     # yellow
    Status.FAIL: "\033[31m",     # red
    Status.SKIP: "\033[90m",     # grey
    Status.ERROR: "\033[35m",    # magenta
}
_RESET = "\033[0m"


def print_human(results: list[CheckResult], stream=sys.stdout) -> None:
    use_color = stream.isatty()

    def colorize(s: Status, text: str) -> str:
        if not use_color:
            return text
        return f"{_STATUS_COLOR[s]}{text}{_RESET}"

    width = max((len(r.name) for r in results), default=20)

    for r in results:
        glyph = colorize(r.status, _STATUS_GLYPH[r.status])
        status_text = colorize(r.status, r.status.value.ljust(5))
        print(
            f"{glyph} [{status_text}] {r.name.ljust(width)}  {r.summary}",
            file=stream,
        )
        if r.fix_hint and r.status in (Status.WARN, Status.FAIL):
            print(f"           → fix: {r.fix_hint}", file=stream)


def print_json(results: list[CheckResult], stream=sys.stdout) -> None:
    summary = {
        "checks": [r.to_dict() for r in results],
        "totals": {s.value: sum(1 for r in results if r.status == s) for s in Status},
    }
    json.dump(summary, stream, indent=2)
    stream.write("\n")


def exit_code(results: list[CheckResult]) -> int:
    """Exit-code для CI / cron / агента:
       0 — все OK или WARN
       1 — есть FAIL
       2 — есть ERROR (баг в самом скрипте)
    """
    has_fail = any(r.status == Status.FAIL for r in results)
    has_error = any(r.status == Status.ERROR for r in results)
    if has_error:
        return 2
    if has_fail:
        return 1
    return 0
