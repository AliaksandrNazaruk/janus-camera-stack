# OPERATION_JOURNAL — Phase 11 (D5)

Replace the **in-memory** long-op machinery in `routes/stream_bindings.py` (`_inflight` dict +
daemon-thread `_spawn_node_op`) with a **minimal, durable** `OperationJournal` + `NodeOperationRunner`.
Goal (explicit): *not* a job framework — a JSON-backed journal that is explainable, testable, and
recoverable after a process restart. Companion: [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) D5.

✅ **IMPLEMENTED** (Phase 11): `services/operation_journal.py` + `services/node_operation_runner.py`
+ startup reaper (`core/events`) + `node_provisioner.is_in_progress`. The route's `_spawn_node_op`
is now a thin wrapper over the runner (the journal lives beside the store in use — its path is
derived from `BIND_STATE_PATH.parent` so the existing test path-redirect covers it). The
in-memory `_inflight` dict + the daemon-thread body were removed. 10 unit tests
(journal / runner / reaper R1 / route-409) + the provision/activate oracle green; full non-e2e
suite is only the known ColorView flake.

## Recon — what exists today

Three async ops fire through `_spawn_node_op(node_id, op, fn, …)` (routes/stream_bindings.py):

| Op | fn | writes (durable, already) | UI polls |
|---|---|---|---|
| `provision` | `node_provisioner.provision` (SSH bootstrap, apt/dpkg) | `NodeEntry.provision_state` + `last_error` | `GET /nodes` |
| `rotate-token` | `node_provisioner.rotate_token` | agent token + state | `GET /nodes` |
| `activate` | `_activate_then_firewall` (SSH activate + on_bind + `firewall_sync.reconcile`) | binding status + bindings | `GET /stream-bindings` |

`_spawn_node_op` (97–117):
- `with _inflight_lock:` → **409** if `node_id in _inflight`, else `_inflight[node_id] = op`.
- daemon `threading.Thread` runs `fn`; on exception, **logs** (the op records its own terminal
  state in the store); `finally` pops `_inflight`.
- Maintenance, host-key confirm, restart/stop/etc. are **synchronous** — not in scope.

**Key facts that shape the design:**
1. **The durable status already lives in the store** (`provision_state` / binding status /
   `last_error`). The journal is **additive** — it does NOT replace that, it adds op tracking.
2. `_inflight` is the *only* in-memory state — a per-node concurrency guard + the 409 label.
   It is lost on restart.
3. Restart mid-op = the real gap: the daemon thread dies, the op never writes its terminal
   state, so `provision_state` can be **stuck** (e.g. `"provisioning"`) forever, and the guard
   is gone. Nothing reaps it.
4. provision/activate are **not cleanly rollback-able** (apt/dpkg, SSH side effects). The current
   contract is "record terminal state, no auto-rollback; degraded is surfaced via last_error".
   Keep that — the journal records, it does not compensate.
5. Single process, low frequency (onboarding), per-node. **No queue / RabbitMQ / asyncio rewrite.**

## The design (minimal)

### `services/operation_journal.py` — JSON-backed, flock'd (mirrors stream_binding_store)

Location: alongside the store — `<dir of sbs.DEFAULT_STATE_PATH>/operations.json`.

Record: `{operation_id, op_type, node_id, status, started_at, finished_at, last_error, detail}`
where `status ∈ {running, succeeded, failed, interrupted}`.

```
begin(node_id, op_type) -> operation_id      # atomic under flock: if a `running` op exists for
                                             # node_id → raise OperationConflict; else write
                                             # `running` + return a new operation_id
finish(operation_id, status, last_error="")  # write terminal state + finished_at
running_for_node(node_id) -> record | None   # the durable replacement for `node_id in _inflight`
list_recent(limit) -> [record]               # ops history (UI/debug)
reap_orphans() -> [record]                   # startup: any still-`running` (their thread died on
                                             # restart) → mark `interrupted`; returns them
```

`operation_id`: stamped by the caller (no Date.now()/random in a workflow-free path — generated
from a monotonic counter persisted in the file, or a uuid at the route boundary).

### `services/node_operation_runner.py` — wraps the daemon thread

```
run(node_id, op_type, fn, *args, **kwargs) -> operation_id:
    op_id = journal.begin(node_id, op_type)        # raises OperationConflict → route maps 409
    def _run():
        try:    fn(*args, **kwargs); journal.finish(op_id, "succeeded")
        except Exception as e:
                log.exception(...); journal.finish(op_id, "failed", str(e))
    Thread(target=_run, daemon=True).start()
    return op_id
```

Keep the **daemon thread** (it works + is immune to the response lifecycle — the original Bug-A
reason). The only change is durability around it.

### Startup reaper (core/events)

On startup call `operation_journal.reap_orphans()`: mark restart-orphaned `running` ops as
`interrupted` and (R1) write a `last_error` note on the NodeEntry + reset `provision_state` to a
retriable value **only if** it is still a known in-progress value (never clobber a terminal one).

### Route changes (thin)

`_spawn_node_op(...)` → `node_operation_runner.run(...)`; `except OperationConflict → 409` with the
same message. The 3 handlers (provision / rotate-token / activate) keep their request parsing,
host-key/transport logic, audit, and `{started: True, poll: …}` response **unchanged**.

## Decisions (resolved 2026-06-20, user-gated)

- **R1 — reaper scope: REAP + UN-STICK.** `reap_orphans()` marks the orphaned op `interrupted`,
  writes a `last_error` note on the NodeEntry, and resets `provision_state` to a retriable value
  **only if** it's still a known in-progress value (never clobber a terminal one). Fixes the
  real "stuck provisioning forever" symptom so the operator can retry.
- **R2 — idempotency key: DEFERRED.** Ship the durable per-node `running` guard as the MVP
  (409 on concurrent op per node, restart-surviving). No `idempotency_key` field yet; revisit if
  a concrete double-submit need appears.
- **R3 — operation_id: uuid4 at the route boundary**, passed into `run`. No shared counter.

## Acceptance

- `_inflight` dict + bare `_spawn_node_op` replaced by `NodeOperationRunner` over
  `OperationJournal`; the 3 async ops fire through it.
- 409-on-concurrent-op-per-node preserved (now **durable** — survives restart).
- Every async op has `operation_id` + durable `status` + `last_error`.
- Startup reaper turns restart-orphaned `running` ops into `interrupted` (+ R1 outcome).
- provision/rotate/activate **logic unchanged**; status fields, audit, poll responses unchanged.
- Tests: journal begin/finish/conflict/reap (unit, tmp file); runner success + failure paths;
  a restart-recovery test (write a `running` record → `reap_orphans` → `interrupted`); the 3
  routes still 200/409 as before.

## Red lines

No change to provision/rotate/activate *behavior* (node_provisioner, firewall_sync, SSH/apt
untouched); no change to the durable status fields or the poll model; no `stream_binding_store`
split; no reconcile/firewall changes; no queue/asyncio rewrite. The runner/journal are new,
additive service modules.

## After Phase 11

The durable runner is the prerequisite for **Phase 12** (`delete_node` / `activate_node_streams`
extraction — long ops with partial-failure/recovery) and keeps **Phase 13** (`stream_binding_store`
split, D2) cleanly separable.
