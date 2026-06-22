# ADR 0001 — Multi-writer coordination на janus.jcfg via flock

**Status**: Accepted (2026-06-14)
**Context**: host_infra Janus role + L4 (janus_camera_page) FastAPI service.

## Problem

`/opt/janus/etc/janus/janus.jcfg` мутируется **тремя независимыми writer'ами**:

| Writer | Process | Trigger | Mutation |
|---|---|---|---|
| NAT updater | `/etc/robot/update-nat-mapping.sh` (cron */15min) | Public IP change | sed `nat_1_1_mapping = "..."` |
| TURN rotator | `/usr/local/bin/janus-turn-rotator` (systemd daily) | Expiry approaching | regex replace `turn_user`, `turn_pwd` |
| L4 admin API | `patch_janus_cfg_with_nat()` (FastAPI) | Operator POST /janus/nat | Rewrite WHOLE `# BEGIN NAT AUTO`…`# END` block |

Mutations **overlap**: L4 rewrites the same lines that cron/rotator patch.

**Race scenario without coordination**:
1. cron NAT updater reads file at t=0
2. TURN rotator reads file at t=0.01
3. cron writes (with new nat_1_1_mapping, old TURN creds)
4. TURN rotator writes (with old nat_1_1_mapping, new TURN creds) → **cron's update lost**

## Options considered

### A. Single-owner pattern (rejected)

One process owns jcfg writes; others publish events to inputs/ dir; owner aggregates + regens.

**Pros**: Cleanest architecture, no race possible.
**Cons**:
- Requires new long-running process (extra deploy artifact + systemd unit)
- All consumers refactored (cron script, rotator, L4 endpoint) to write structured events
- Latency: event → regen → janus reload chain
- Over-engineered for current scale (3 writers, low frequency)

### B. flock coordination (accepted)

All writers acquire exclusive lock на `/var/lock/janus-jcfg.lock` перед read-modify-write. POSIX flock works across bash + Python uniformly.

**Pros**:
- Minimal code change (~30 LOC per writer)
- Standard pattern (flock(1), `fcntl.LOCK_EX`)
- Cross-language: bash `flock 200>$LOCK` + Python `fcntl.LOCK_EX` interoperate
- Timeout primitive prevents deadlock (60s default)
- Idempotent — lock file auto-created if missing

**Cons**:
- Writers must remember to acquire (no compile-time guarantee)
- Per-machine only (не работает на NFS — but jcfg is local file)
- TOCTOU: must re-read inside lock (we do this)

### C. Move L4 logic into NAT updater (rejected)

L4 stops writing jcfg; instead invokes `/etc/robot/update-nat-mapping.sh` or equivalent.

**Pros**: Removes one writer.
**Cons**: L4's writes are **richer** than cron's — L4 rewrites whole NAT block (ice_*, stun_*, turn_*, port range) from JSON body; cron only patches one line. Different semantic.

## Decision

**Option B (flock)**. Minimum changes, cross-language uniformly, sufficient для текущего scale.

## Implementation

### Lock contract

| Property | Value |
|---|---|
| Path | `/var/lock/janus-jcfg.lock` |
| Mode | `LOCK_EX` (exclusive) |
| Timeout | 60s (configurable via env var) |
| Created | If missing (auto-create with mode 0644) |
| Released | On context exit (success OR exception) |

### Per-writer implementation

**bash** (`janus-nat-updater.sh`):
```bash
(
    flock -w "$LOCK_TIMEOUT" 200 || exit 2
    # read-modify-write here
) 200>"$LOCK_FILE"
```

**Python** (TURN rotator, L4):
```python
@contextlib.contextmanager
def _jcfg_lock(timeout=60, path="/var/lock/janus-jcfg.lock"):
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o644)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError(...)
                time.sleep(0.5)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
```

### Critical: re-read внутри lock (TOCTOU)

Все writers re-read jcfg внутри lock context. Optimistic check (без lock) разрешён только для decisions (need-rotation? IP-changed?), не для writes.

## Verification

Three integration tests confirmed работу:

1. **bash NAT updater test**: bash holder + bash NAT waiter → waiter ждёт `~4s` (matches holder duration). См. session log.
2. **Python TURN rotator test**: same pattern с `_force --no-restart`. См. `test_jcfg_lock_blocks_then_acquires`.
3. **Python L4 test**: bash holder + L4 `_jcfg_lock()` waiter → waiter ждёт `2s`. См. session log.

Unit tests covering `jcfg_lock()`:
- `test_jcfg_lock_basic_acquire_release`
- `test_jcfg_lock_reacquire_after_release`
- `test_jcfg_lock_creates_parent_dir`
- `test_jcfg_lock_blocks_then_acquires` (multiprocessing-based real contention)
- `test_jcfg_lock_timeout_raises`

## Migration path к Option A (если scale потребует)

Если число writers вырастет (5+) или потребуется audit log → пересмотреть на single-owner.
Сейчас flock держит на текущих 3 writer'ах.

## References

- POSIX flock(2): https://man7.org/linux/man-pages/man2/flock.2.html
- Python `fcntl.flock`: https://docs.python.org/3/library/fcntl.html#fcntl.flock
- Implementation: `roles/janus/files/janus-{nat-updater.sh,turn-rotator.py}`, `janus_camera_page/app/services/nat_config.py`
