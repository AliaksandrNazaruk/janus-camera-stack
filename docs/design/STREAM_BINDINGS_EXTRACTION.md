# STREAM_BINDINGS_EXTRACTION — Phase 10 (D1)

Drain `routes/stream_bindings.py` (888L, the main fat route — see
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) D1) of orchestration,
**one small vertical at a time**. Route stays DTO + auth + endpoint mapping; orchestration moves
to `app/application/stream_bindings/`. Same discipline as the route-purity campaign:
recon → design note → characterization → move verbatim → re-point → one commit per vertical.

## First vertical: `restart_binding` + `stop_binding` — ✅ DONE

Landed: `application/stream_bindings/{commands,results,restart_binding,stop_binding}.py`
(FastAPI-free; route maps domain errors → 404/400/502). stream_bindings.py 888→856. Oracle
(`test_operator_console.py` restart/stop ×5) preserved + 11 use-case unit tests
(`test_stream_bindings_usecases.py`). Decision taken: domain-results + route-maps (begins D3).

Picked first (per review) for the smallest blast radius. Recon confirms both are **synchronous,
self-contained, and clean**:

| | `restart_stream_binding` (routes:765) | `stop_stream_binding` (routes:793) |
|---|---|---|
| Async/daemon? | **no** — synchronous, NOT via `_spawn_node_op`/`_inflight` | **no** |
| Local branch (`LOCAL_PRODUCER`) | `sensor_lifecycle.set_desired(serial,sensor,True)` + `_encoder_action("restart","rs-stream",sensor)` | `sensor_lifecycle.stop(serial,sensor)` |
| Remote branch | `node_client.get_node_client(...).restart_stream(node,sensor)` | `…stop_stream(...)` + on success `sbs.set_status(CONFIGURED_OFFLINE)` + `sbs.set_fdir_enabled(False)` |
| Side effects | audit `stream_bindings.binding.restart` | audit `stream_bindings.binding.stop`; store status + fdir writes |
| Errors | 404 unknown binding; 502 op failed | 404; 400 unsupported sensor; 502 op failed |
| Returns | `{binding_id, ok, detail}` | `{binding_id, ok, detail}` |

Deps used (all existing public methods — **no store split**): `sbs.get_binding / set_status /
set_fdir_enabled` (+ `StreamMode`, `StreamStatus`), `sensor_lifecycle.{set_desired,_encoder_action,
stop, UnsupportedSensor, LifecycleError}`, `node_client.get_node_client`, `audit`,
`BIND_STATE_PATH` / `ALLOC_STATE_PATH`.

## Target layout

```
routes/stream_bindings.py        thin: parse binding_id + auth + map result/errors → HTTP
app/application/stream_bindings/
    __init__.py
    commands.py    RestartBindingCommand(binding_id) / StopBindingCommand(binding_id)
    results.py     BindingOpResult(binding_id, ok, detail) + domain errors
                   (BindingNotFound, UnsupportedSensorError)
    restart_binding.py   restart_binding(cmd) -> BindingOpResult   (raises BindingNotFound)
    stop_binding.py      stop_binding(cmd)    -> BindingOpResult   (raises BindingNotFound / UnsupportedSensorError)
```

## Key decision — error mapping (no FastAPI in the use-case)

The current handlers raise `HTTPException` (404/400/502) inline. The committed layout
(`commands.py` + `results.py`) implies the cleaner option, which also begins paying down **D3**
(application/ leaks FastAPI) for this vertical:

- **Use-case returns `BindingOpResult` and raises plain domain errors** (`BindingNotFound`,
  `UnsupportedSensorError`); the **route** maps: `BindingNotFound`→404, `UnsupportedSensorError`→400,
  `result.ok is False`→502, else return `{binding_id, ok, detail}`. The use-case imports **no**
  `fastapi`.
- (Lighter alternative, if preferred: keep `HTTPException` in the use-case like C-04 — faster, but
  perpetuates D3. Not recommended given the chosen layout.)

Recommendation: the clean option. It's only two small error types and keeps `application/` FastAPI-free.

## Acceptance

- Routes keep same paths/methods/auth (`_RL` rate-limit dep)/response shape `{binding_id, ok, detail}`.
- Behavior byte-identical: LOCAL_PRODUCER vs remote branches; the stop **set_status(CONFIGURED_OFFLINE)
  + set_fdir_enabled(False)** side effects (FDIR-off so a stop sticks) preserved exactly; same audit
  events + outcomes; same status codes (404/400/502).
- Existing oracle green: `test_operator_console.py` (`/restart` ×2, `/stop` ×3). Characterization added
  for both branches + the stop side-effects + error cases, then re-pointed.
- One commit. `restart_binding` + `stop_binding` orchestration no longer in the route module.

## Red lines (do NOT touch in this vertical)

`stream_binding_store.py` split; `NodeOperationRunner` / operation journal / `_inflight` / daemon
thread; `delete_node`; `activate_node_streams`; firewall / `reconcile` logic; Janus control plane;
`sensor_lifecycle` / `node_client` internals (call them, don't change them). Characterize first,
move verbatim, identical assertions.

## Progress

- ✅ **10.1** `restart_binding` + `stop_binding` (`eb62b38`)
- ✅ **10.2** fdir-toggle → `set_fdir` (`004420b`) (`LocalFdirNotToggleable`; returns
  `StreamBinding`, route renders `BindingOut`)
- ✅ **10.3** remove binding → `remove_binding` (`0720db1`) (`LocalBindingNotRemovable` +
  `RemoveBindingResult`). (Flagged a "D9" Janus-teardown bug here — later **RETRACTED**: I had
  missed that `janus_admin.destroy_mountpoint` is `@_with_handle`-decorated, so calling it with
  only `mp_id`/`mp_secret` is correct. No bug; see KNOWN_LIMITATIONS.)
- ✅ **10.4** ensure-janus → `ensure_janus` (`EnsureJanusResult` + `EnsureJanusLocalRejected`).
  Delegates Janus work to `binding_provision.ensure_janus` (owns session/handle) — clean,
  **no latent bug**. No 502 path (a failed provision returns 200 with the outcome status).
- ✅ **10.5** tuning → `get_tuning` + `set_tuning` (GET+POST; `LocalTuningRejected`,
  `InvalidRotation`, `NoTuningFields`, `NodeAgentError`→502). Validation moved into the use-case.

## Phase 10 small verticals — CLOSED

`application/stream_bindings/` now holds **7 use-cases** (restart, stop, set_fdir, remove,
ensure_janus, get_tuning, set_tuning) over `commands.py` / `results.py`, **all FastAPI-free**;
every per-binding route handler is parse + call + map. Route **888 → 852**.

Deferred to explicitly-gated phases (NOT small verticals):
- **D9** — fix the dead `remove` Janus teardown (a behavior change in the control plane).
- **Phase 11** — `_inflight` + daemon thread (`_spawn_node_op`) → durable `OperationJournal` runner.
- **Heavy topology** — `delete_node`, `activate_node_streams`, fleet reconcile (long ops, partial
  failure / recovery — pair with Phase 11).
- `stream_binding_store.py` split (**D2**) — only after the heavy callers are thinned.
