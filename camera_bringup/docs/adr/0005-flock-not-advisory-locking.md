# ADR 0005: flock vs advisory locking для apply concurrency

## Status

Accepted (2026-06-14)

## Context

Apply операции мутируют state (udev rules, sysfs, fingerprint). Если cron-based
verify запустится во время оператор-apply — race condition: оператор пишет
файл, verify читает partial state, делает неверный output.

Нужен mechanism чтобы:
- Только один apply работает одновременно (exclusive)
- Verify может работать параллельно с apply (read-only — нет state mutation)
- Concurrent apply attempt fails fast, не hangs

Options:
1. **fcntl.flock** на отдельный lock file
2. **PID-file based**: проверка existence + signal(0)
3. **Advisory file locking** (lockf, fcntl.lockf)
4. **systemd-managed**: запускать apply через `systemd-run --transient` с
   `StartLimitInterval`/`StartLimitBurst`

## Decision

**flock на `/run/camera_bringup-<instance>.lock`**.

## Consequences

Положительные:
- **Kernel-enforced**: flock — реальная kernel-level примитива, не cooperative
- **Per-instance**: `/run/camera_bringup-<instance>.lock` — каждая instance
  имеет свой lock. Multi-instance apply работает параллельно.
- **Automatic cleanup**: process die → flock auto-release. PID-file подход
  требует manual cleanup при kill -9.
- **Non-blocking by default**: `LOCK_EX | LOCK_NB` → immediate fail с
  `LockBusyError`. Хорошее UX в CLI.
- **Optional blocking mode**: `apply_lock(blocking=True, timeout=30)` если
  нужно ждать (для future scripts).

Отрицательные:
- **Только same-host**: NFS-mounted lock file unreliable. Acceptable — наш
  use case local-only.
- **`/run/` writable required**: на Pi5 это OK (tmpfs). Fallback на
  `~/.camera_bringup.lock` если /run не writable.

## Implementation

```python
# fixer.py
@contextlib.contextmanager
def apply_lock(path=LOCK_FILE, *, blocking=False, timeout=30):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        if blocking:
            # poll loop with timeout
            ...
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LockBusyError(...)
        os.write(fd, f"{os.getpid()}\n".encode())  # для диагностики
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
```

`run_fixer()` оборачивает execution в `with apply_lock():`. Pre-check и
post-check без lock (они read-only).

## Alternatives rejected

- **PID-file**: race condition при cleanup, не handles kill -9
- **fcntl.lockf**: similar to flock но process-level locking — наш use case
  OK с file-level
- **systemd-run**: heavy, требует systemd dependency для unit tests

## References

- `apply_lock()` в `fixer.py`
- `tests/unit/test_apply_lock.py` — 5 tests включая concurrent acquire
- CONTRACT.md §5 "Concurrency safety"
