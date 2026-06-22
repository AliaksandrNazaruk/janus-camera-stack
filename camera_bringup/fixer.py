"""Базовая инфраструктура для fixers (apply mode).

Контракт:
  - Один fixer 1:1 покрывает один check (одно и то же имя).
  - Apply делает plan-and-confirm: сначала возвращает список Action'ов
    (для dry-run preview), потом выполняет.
  - Каждый destructive write делает backup (atomic, в .pause-state/backup/).
  - После apply — re-run соответствующего check'а, drift должен закрыться.
  - Fail-fast: первый failed Action останавливает fixer; runner может
    продолжать со следующим fixer'ом (--continue-on-fail) или нет.
  - Если check уже OK → fixer возвращает SKIPPED, ничего не делает.

Action types:
  WriteFile(path, content)   — заменить файл (с backup)
  EditFile(path, edit_fn)    — patch файла через функцию (с backup)
  Run(cmd)                   — выполнить subprocess
  Reload(unit)               — systemctl reload
  Restart(unit)              — systemctl restart
  Backup(src, dst_dir)       — снять backup

Все Action'ы идемпотентны: если состояние уже соответствует — no-op.
"""
from __future__ import annotations

import abc
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from camera_bringup.check import CheckFn, CheckResult, Status, safe_run
from camera_bringup.spec import LOCK_FILE

# ── Action primitives ─────────────────────────────────────────────────

class ActionStatus(str, Enum):
    PENDING = "PENDING"      # plan-only state
    NOOP = "NOOP"            # state уже соответствует, ничего не сделали
    APPLIED = "APPLIED"      # выполнен успешно
    FAILED = "FAILED"        # выполнение упало


@dataclass
class Action:
    """Описание одного шага fixer'а."""
    kind: str                          # "write_file" | "run" | "restart" | ...
    description: str                   # для preview/log
    target: str                        # path/cmd/unit
    payload: str | None = None      # content / args
    status: ActionStatus = ActionStatus.PENDING
    error: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "target": self.target,
            "status": self.status.value,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class FixerStatus(str, Enum):
    SKIPPED = "SKIPPED"      # check уже OK, fixer не нужен
    APPLIED = "APPLIED"      # все actions APPLIED/NOOP
    FAILED = "FAILED"        # хотя бы один FAILED
    UNFIXED = "UNFIXED"      # actions OK, но post-verify всё ещё показывает drift


@dataclass
class FixerResult:
    name: str
    status: FixerStatus
    summary: str
    actions: list[Action] = field(default_factory=list)
    pre_check: CheckResult | None = None    # check ДО apply
    post_check: CheckResult | None = None   # check ПОСЛЕ apply

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "actions": [a.to_dict() for a in self.actions],
            "pre_check": self.pre_check.to_dict() if self.pre_check else None,
            "post_check": self.post_check.to_dict() if self.post_check else None,
        }


# ── Action executors ─────────────────────────────────────────────────

from camera_bringup.spec import BRINGUP_HOME  # noqa: E402  # placed after LOCK_FILE consts

BACKUP_DIR = BRINGUP_HOME / ".pause-state" / "backup"


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


class LockBusyError(RuntimeError):
    """Не удалось взять flock — другой apply сейчас работает."""


@contextlib.contextmanager
def apply_lock(path: str = LOCK_FILE, *, blocking: bool = False, timeout: float = 30) -> Iterator[None]:
    """flock-based exclusive lock для apply операций.

    Защищает от concurrent execution (например cron verify во время оператор-apply
    может race'ить на одном файле).

    blocking=False (default): сразу raise LockBusyError если занят
    blocking=True: ждёт до timeout сек

    Verify-only операции (read system without write) НЕ нуждаются в lock —
    они не модифицируют state.
    """
    # Create lock file (boris-writable; /run is tmpfs so reset on reboot)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    except PermissionError:
        # /run может быть не writable пользователем — fallback на $HOME
        fallback = str(Path.home() / ".camera_bringup.lock")
        fd = os.open(fallback, os.O_WRONLY | os.O_CREAT, 0o644)

    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        if blocking:
            # Имитация timeout через alarm — но проще poll'ить
            start = time.monotonic()
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() - start > timeout:
                        raise LockBusyError(
                            f"apply lock {path} not acquired within {timeout}s"
                        ) from exc
                    time.sleep(0.1)
        else:
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError as exc:
                raise LockBusyError(
                    f"apply lock {path} held by another process"
                ) from exc

        # Записываем pid для диагностики
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())

        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def execute(action: Action) -> Action:
    """Выполнить action, обновить его status/error/duration. Возвращает он же."""
    start = time.monotonic()
    try:
        if action.kind == "write_file":
            _execute_write_file(action)
        elif action.kind == "run":
            _execute_run(action)
        elif action.kind == "restart":
            _execute_systemctl(action, "restart")
        elif action.kind == "reload":
            _execute_systemctl(action, "reload")
        elif action.kind == "backup":
            _execute_backup(action)
        elif action.kind == "chmod_exec":
            _execute_chmod_exec(action)
        else:
            action.status = ActionStatus.FAILED
            action.error = f"unknown action kind: {action.kind}"
    except Exception as exc:
        action.status = ActionStatus.FAILED
        action.error = f"{type(exc).__name__}: {exc}"
    action.duration_ms = int((time.monotonic() - start) * 1000)
    return action


def _execute_write_file(action: Action) -> None:
    path = Path(action.target)
    new_content = action.payload or ""

    # No-op if content already matches
    if path.is_file():
        existing = path.read_text()
        if existing == new_content:
            action.status = ActionStatus.NOOP
            return

    # Backup if file exists
    if path.is_file():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_name = f"{path.name}.{_ts()}.bak"
        shutil.copy2(path, BACKUP_DIR / backup_name)

    # Atomic write (tmp + rename)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(new_content)
    os.replace(tmp, path)
    action.status = ActionStatus.APPLIED


def _execute_run(action: Action) -> None:
    cmd = action.target.split() if action.payload is None else [action.target, *action.payload.split()]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        action.status = ActionStatus.FAILED
        action.error = (result.stderr or result.stdout or "").strip().splitlines()[-1] if result.stderr or result.stdout else f"exit {result.returncode}"
        return
    action.status = ActionStatus.APPLIED


def _execute_systemctl(action: Action, verb: str) -> None:
    result = subprocess.run(
        ["systemctl", verb, action.target],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        action.status = ActionStatus.FAILED
        action.error = (result.stderr or "").strip()
        return
    action.status = ActionStatus.APPLIED


def _execute_backup(action: Action) -> None:
    src = Path(action.target)
    if not src.is_file():
        action.status = ActionStatus.NOOP
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_name = f"{src.name}.{_ts()}.bak"
    shutil.copy2(src, BACKUP_DIR / backup_name)
    action.status = ActionStatus.APPLIED


def _execute_chmod_exec(action: Action) -> None:
    path = Path(action.target)
    if not path.is_file():
        action.status = ActionStatus.FAILED
        action.error = f"{path} не существует"
        return
    mode = path.stat().st_mode
    desired = mode | 0o111  # u+x g+x o+x
    if mode == desired:
        action.status = ActionStatus.NOOP
        return
    os.chmod(path, desired)
    action.status = ActionStatus.APPLIED


# ── Fixer ABC + helpers ───────────────────────────────────────────────

class Fixer(abc.ABC):
    """Базовый класс для всех fixers.

    Контракт:
      - name матчит соответствующий check
      - requires_root() = True если apply нуждается в sudo
      - plan(ctx) возвращает список Action'ов в pending состоянии
      - apply(ctx) выполняет их и возвращает FixerResult

    Конструктор fixer'а должен принимать пустые args; всё через ctx.
    """

    #: должно совпадать с именем check'а (см. checks/ALL_CHECKS)
    name: str

    requires_root: bool = False

    @abc.abstractmethod
    def plan(self, ctx: dict[str, Any]) -> list[Action]:
        """Вернуть список действий, которые apply выполнил бы. Без side-effects."""
        raise NotImplementedError


def run_fixer(
    fixer: Fixer,
    check_fn: CheckFn,
    ctx: dict[str, Any],
    *,
    dry_run: bool = False,
    skip_lock: bool = False,
) -> FixerResult:
    """Выполнить fixer:
       1. pre-check — если уже OK → SKIPPED
       2. plan
       3. dry-run? → возвращаем сразу с PENDING actions
       4. apply (под flock — защита от concurrent apply)
       5. post-check — verify drift закрылся

    skip_lock — для unit tests; в production не использовать.
    """
    pre = safe_run(fixer.name, check_fn, ctx)

    # Если check уже OK — fixer не нужен
    if pre.status == Status.OK:
        return FixerResult(
            name=fixer.name,
            status=FixerStatus.SKIPPED,
            summary=f"check OK, fixer не нужен ({pre.summary})",
            pre_check=pre,
        )

    actions = fixer.plan(ctx)

    if dry_run:
        return FixerResult(
            name=fixer.name,
            status=FixerStatus.SKIPPED,
            summary=f"dry-run: {len(actions)} action(s) planned",
            actions=actions,
            pre_check=pre,
        )

    # Acquire exclusive lock (no concurrent apply allowed)
    if skip_lock:
        lock_ctx = contextlib.nullcontext()
    else:
        try:
            lock_ctx = apply_lock()
        except LockBusyError as exc:
            return FixerResult(
                name=fixer.name,
                status=FixerStatus.FAILED,
                summary=f"concurrent apply detected: {exc}",
                actions=actions,
                pre_check=pre,
            )

    from camera_bringup.logger import get_logger
    log = get_logger("fixer")
    log.info(
        "fixer apply started",
        extra={"fixer": fixer.name, "actions_count": len(actions)},
    )

    with lock_ctx:
        # Execute actions one by one, fail-fast on first FAILED
        for action in actions:
            execute(action)
            log.info(
                "action executed",
                extra={
                    "fixer": fixer.name,
                    "action_kind": action.kind,
                    "action_target": action.target,
                    "action_status": action.status.value,
                    "duration_ms": action.duration_ms,
                    "error": action.error,
                },
            )
            if action.status == ActionStatus.FAILED:
                log.warning(
                    "fixer apply aborted on action failure",
                    extra={"fixer": fixer.name, "failed_action": action.kind},
                )
                return FixerResult(
                    name=fixer.name,
                    status=FixerStatus.FAILED,
                    summary=f"action failed: {action.kind} {action.target} — {action.error}",
                    actions=actions,
                    pre_check=pre,
                )

        log.info("fixer apply completed", extra={"fixer": fixer.name})

    # Re-run check to verify drift closed
    post = safe_run(fixer.name, check_fn, ctx)

    if post.status == Status.OK:
        return FixerResult(
            name=fixer.name,
            status=FixerStatus.APPLIED,
            summary=f"drift closed: {post.summary}",
            actions=actions,
            pre_check=pre,
            post_check=post,
        )

    return FixerResult(
        name=fixer.name,
        status=FixerStatus.UNFIXED,
        summary=f"actions ok, но drift остался: {post.summary}",
        actions=actions,
        pre_check=pre,
        post_check=post,
    )


# ── Printers ─────────────────────────────────────────────────────────

_FIXER_GLYPH = {
    FixerStatus.SKIPPED: "·",
    FixerStatus.APPLIED: "✓",
    FixerStatus.FAILED: "✗",
    FixerStatus.UNFIXED: "!",
}

_FIXER_COLOR = {
    FixerStatus.SKIPPED: "\033[90m",
    FixerStatus.APPLIED: "\033[32m",
    FixerStatus.FAILED: "\033[31m",
    FixerStatus.UNFIXED: "\033[33m",
}
_RESET = "\033[0m"


def print_fixer_results(results: list[FixerResult], stream=sys.stdout) -> None:
    use_color = stream.isatty()

    def col(s: FixerStatus, t: str) -> str:
        return f"{_FIXER_COLOR[s]}{t}{_RESET}" if use_color else t

    width = max((len(r.name) for r in results), default=20)
    for r in results:
        glyph = col(r.status, _FIXER_GLYPH[r.status])
        status_text = col(r.status, r.status.value.ljust(8))
        print(f"{glyph} [{status_text}] {r.name.ljust(width)}  {r.summary}", file=stream)
        # show actions in non-trivial cases
        if r.status in (FixerStatus.FAILED, FixerStatus.UNFIXED) or any(
            a.status == ActionStatus.PENDING for a in r.actions
        ):
            for a in r.actions:
                marker = {
                    ActionStatus.PENDING: "·",
                    ActionStatus.NOOP: "=",
                    ActionStatus.APPLIED: "✓",
                    ActionStatus.FAILED: "✗",
                }[a.status]
                err = f"  [{a.error}]" if a.error else ""
                print(f"           {marker} {a.kind:<12} {a.description}{err}", file=stream)


def fixer_exit_code(results: list[FixerResult]) -> int:
    """0 — все SKIPPED или APPLIED.
       1 — есть UNFIXED.
       2 — есть FAILED.
    """
    if any(r.status == FixerStatus.FAILED for r in results):
        return 2
    if any(r.status == FixerStatus.UNFIXED for r in results):
        return 1
    return 0
