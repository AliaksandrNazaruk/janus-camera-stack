"""SystemPort — abstraction layer для system access (ports/adapters pattern).

Hexagonal architecture: checks/fixers зависят от protocol, не от concrete
os/subprocess вызовов. Это enables clean unit testing через FakeSystemPort
без monkeypatch hell.

Status: **skeleton + 1 reference migration (c02_usb_power)**. Остальные 10
checks продолжают использовать direct os/subprocess + monkeypatch в tests.
Migration к SystemPort инкрементальная — делается при touching конкретного
check'а. См. ADR 0006.

Usage в check:
    def check(ctx, system: SystemPort = None) -> CheckResult:
        system = system or RealSystemPort()
        content = system.read_file("/sys/.../control")
        ...

Usage в test:
    fake = FakeSystemPort(files={"/sys/.../control": "on"})
    result = check({}, system=fake)
"""
from __future__ import annotations

import glob as _glob
import os
import subprocess
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ── RunResult — common return type для subprocess ───────────────────

@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ── Protocol ──────────────────────────────────────────────────────────

@runtime_checkable
class SystemPort(Protocol):
    """Минимальный set операций для checks/fixers.

    Все методы должны быть deterministic (для same input — same output)
    и pure (no internal state mutation visible externally, кроме файловой
    системы для write ops).
    """

    def read_file(self, path: str) -> str | None: ...
    """Прочитать файл; None если не существует или нет прав."""

    def exists(self, path: str) -> bool: ...
    """True если path существует (file/dir/symlink)."""

    def glob(self, pattern: str) -> list[str]: ...
    """Найти paths по pattern (как glob.glob)."""

    def run(self, cmd: list[str], *, timeout: float = 10) -> RunResult: ...
    """Выполнить subprocess. Возвращает RunResult."""


# ── Real implementation (production) ─────────────────────────────────

class RealSystemPort:
    """Use real os/subprocess. Default для production."""

    def read_file(self, path: str) -> str | None:
        try:
            with open(path) as f:
                return f.read()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def glob(self, pattern: str) -> list[str]:
        return _glob.glob(pattern)

    def run(self, cmd: list[str], *, timeout: float = 10) -> RunResult:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return RunResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except FileNotFoundError:
            return RunResult(returncode=127, stderr=f"command not found: {cmd[0]}")
        except subprocess.TimeoutExpired:
            return RunResult(returncode=-9, stderr=f"timeout ({timeout}s)")


# ── Fake implementation (tests) ──────────────────────────────────────

@dataclass
class FakeSystemPort:
    """In-memory fake. Регистрируем files/cmd ответы заранее, потом checks
    используют этот port вместо real os.

    Usage:
        fake = FakeSystemPort(
            files={"/sys/.../control": "on", "/sys/.../persist": "0"},
            run_responses={("udevadm", "control", "--reload"): RunResult(0)},
        )
    """
    files: dict[str, str] = field(default_factory=dict)
    globs: dict[str, list[str]] = field(default_factory=dict)
    run_responses: dict[tuple, RunResult] = field(default_factory=dict)

    # Для assertions в tests: список cmd'ов которые были вызваны
    run_history: list[list[str]] = field(default_factory=list)

    def read_file(self, path: str) -> str | None:
        return self.files.get(path)

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.globs

    def glob(self, pattern: str) -> list[str]:
        # Точное совпадение pattern если зарегистрирован, иначе пустой
        return self.globs.get(pattern, [])

    def run(self, cmd: list[str], *, timeout: float = 10) -> RunResult:
        self.run_history.append(list(cmd))
        key = tuple(cmd)
        if key in self.run_responses:
            return self.run_responses[key]
        return RunResult(returncode=0, stdout="", stderr="(default fake response)")


def default_system() -> SystemPort:
    """Convenience: return RealSystemPort instance. Используется в checks
    как fallback когда port не передан явно."""
    return RealSystemPort()
