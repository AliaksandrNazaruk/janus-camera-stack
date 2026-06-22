# SENSOR_LIFECYCLE_SPLIT — Phase 4 / A-04 recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md). De-mixes the
`services/sensor_lifecycle.py` "god service" (A-04) into focused modules behind an UNCHANGED facade,
the same proven pattern as the D2 god-store split (`stream_binding_store.py` → package + facade).
Behavior-preserving. No code until GO.

## Recon — what's in the 393-line file (verified 2026-06-21)
Six cohesive-but-distinct concerns:
1. **encoder-admin port** — `_ENCODER_ADMIN_CMD = ["sudo","/usr/local/bin/encoder-admin"]`,
   `_encoder_status`, `_encoder_action` (the scoped-sudo CLI adapter; raises `LifecycleError`).
2. **readiness probes** — `is_running`, `mux_running`, `encoder_running` (thin wrappers over `_encoder_status`).
3. **contract/tuning env store** — `_contract_path`, `_tuning_path`, `_write_contract_env`,
   `_ensure_default_tuning_env` (atomic writes to `/etc/robot/rs-<sensor>.{contract,tuning}.env`).
4. **cross-process lock** — `_sensor_lock` (per-(serial,sensor) flock; cross-cutting).
5. **activation** — `initialize` / `_initialize_locked` (the orchestrator: alloc → contract/tuning →
   mux start → Janus create_mountpoint → rs-stream start → readiness poll → set_desired).
6. **stop** — `stop` / `_stop_locked`.
Plus constants (`COLOR_MP_ID/RTP_PORT/ENCODER_INSTANCE`, `_SENSOR_META`, `MP_DEFAULT_SECRET`) and
exceptions (`UnsupportedSensor`, `LifecycleError`). Deps: `janus_admin`, `mountpoint_allocator`, `system.run`.

The "**mountpoint port**" the audit lists is ALREADY external (`janus_admin.create/destroy/list_mountpoints`
+ `mountpoint_allocator`); the orchestrator just calls those services. No new extraction there (like Phase 3-5).

## The facade contract (28 callers — must stay byte-stable)
`sensor_lifecycle` has **28 importers** (use-cases, routes, services, tools, 11 test files). The symbols
they reference as `sensor_lifecycle.X` (the facade MUST keep exposing them):
- public API: `initialize`, `stop`, `is_running`, `mux_running`, `encoder_running`
- exceptions: `UnsupportedSensor`, `LifecycleError`
- externally-used "privates": `_encoder_action` (called by `application/stream_bindings/restart_binding.py`
  + `services/node_client.py`), `_write_contract_env`, `_sensor_lock` (tests)
- constants: `COLOR_MP_ID`, `COLOR_RTP_PORT`, `COLOR_ENCODER_INSTANCE`, `_SENSOR_META`, `MP_DEFAULT_SECRET`
- re-exported from `mountpoint_allocator`: `set_desired` (used as `sensor_lifecycle.set_desired`) — keep the
  module's current `from mountpoint_allocator import (...)` re-exports so any `sensor_lifecycle.<alloc-sym>` holds.

### The patch seams (what constrains the cross-call wiring) — GOOD NEWS
Every test patch of `sensor_lifecycle` is a **caller-boundary mock**, never an internal-seam patch:
- `test_device_camera.py` patches `sensor_lifecycle.initialize` (×3) / `.stop` — mocking the route's view.
- `test_streams_dashboard.py` patches `sensor_lifecycle.is_running` — mocking the dashboard's view.
No test patches an internal helper (e.g. `_encoder_action`) and then calls `initialize` expecting the patch
to propagate. **So the split's modules may cross-call via direct imports** — no need for the module-qualified
"patch-at-the-facade" gymnastics. Low risk. (Still: the facade re-exports the public names so those
caller-boundary patches keep landing on `sensor_lifecycle.<name>`.)

### Circular-dependency wrinkle
`_encoder_action` raises `LifecycleError`, so the encoder-admin module can't import it FROM the orchestrator
(cycle). The exceptions must live in a base module both import. → an `errors`/base module is required.

## Plan — sub-commits (tests-first, suite green between, facade unchanged each step)
1. **char** — confirm the 28-caller contract with a focused import/attr test (assert every facade symbol
   above resolves) so any drop is caught; confirm full non-e2e green as the baseline.
2. **errors + encoder-admin port** — move `UnsupportedSensor`/`LifecycleError` to a base module; move
   `_ENCODER_ADMIN_CMD`/`_encoder_status`/`_encoder_action` + the readiness probes into an encoder-admin
   adapter; facade re-exports them. Suite green.
3. **contract env store** — move the contract/tuning writers into a config-store module; facade re-exports.
4. **(if package form) fold the orchestrator** — `initialize`/`stop`/`_sensor_lock` become the package's
   pipeline module; `__init__` is the facade. Suite green.
5. **guard** — lock the encoder-admin chokepoint: `["sudo", ".../encoder-admin"]` / `_ENCODER_ADMIN_CMD`
   invoked ONLY from the encoder-admin module (analogous to guard #14 systemctl + #16 SSHTransport).

## Open decisions to gate (GO before any code)
- **D1 — package vs sibling modules:** **(A) package** `services/sensor_lifecycle/` with `__init__` facade +
  `errors`/`encoder_admin`/`contract_env`/`pipeline` (matches the D2 god-store split; CONTAINS the split so it
  doesn't bloat the already-broad `services/` flagged by A-06). **(B) sibling modules** in `services/`
  (`sensor_encoder_admin.py`, `sensor_contract_env.py`, `sensor_errors.py`) + keep `sensor_lifecycle.py` as the
  orchestrator/facade (lighter, but adds loose files to `services/`). **Lean: (A) package** — consistent with
  D2, better for A-06.
- **D2 — granularity:** extract the encoder-admin port + readiness + contract-env + errors; KEEP
  activation+stop+lock together as the cohesive orchestrator (do NOT split activation from stop — they share
  the lock + the color/dynamic branching; splitting them is DDD-for-DDD). **Lean: as stated.**
- **D3 — readiness home:** `is_running`/`mux_running`/`encoder_running` live WITH the encoder-admin port
  (they're thin `_encoder_status` wrappers) vs their own module. **Lean: with the port** (one cohesive adapter).
- **D4 — guard:** add the encoder-admin chokepoint guard (#18) in the same close, mirroring #14/#16. **Lean: yes.**

## Red lines
Behavior-preserving: the EXACT `initialize`/`stop` orchestration (color-static vs depth/IR-dynamic branches,
the lock order sensor-flock→allocator-flock, the 3s readiness poll, set_desired timing, the "already exists"
mountpoint tolerance). The **facade (`sensor_lifecycle`) public + externally-used-private + re-exported
symbols stay byte-identical** for all 28 callers — never make a caller change imports. Tests-first; never
weaken a characterization assertion. `realsense_mux.py` untouched. No new behavior — pure re-housing.

## Status — DONE (2026-06-21)
Decisions taken: **D1 (A)** package; **D2** activation+stop+lock kept cohesive in `pipeline.py`;
**D3** readiness lives with the encoder-admin port; **D4 guard #18 DROPPED** (see below).
- **4-1** `1b57685` — facade-contract characterization test (`tests/test_sensor_lifecycle_facade.py`):
  every public symbol + externally-used private + constant + re-exported allocator symbol must resolve on
  the facade. Passes against the pre-split module and across the split (the lock).
- **4-2** `f891079` — `services/sensor_lifecycle.py` → `services/sensor_lifecycle/` package: `errors` /
  `encoder_admin` (port + readiness) / `contract_env` / `pipeline` (initialize/stop + `_sensor_lock` +
  constants) + the `__init__` facade re-exporting the full contract. Bodies moved VERBATIM — the package
  inherits the original's exact ruff baseline (20 pre-existing errors, same codes), **zero net-new lint
  debt**. Re-pointed **8 internal-seam test patches** (`test_sensor_lock` lock-config → `pipeline`,
  `test_binding_provision._contract_path` → `contract_env`) to their new source submodules: these patch a
  symbol and expect the SAME-module function to see it, which the facade re-export deliberately does NOT
  propagate (the recon's patch-grep missed `monkeypatch.setattr` — caught at suite-run, re-pointed
  "patch-at-the-source" with identical assertions). The `_encoder_action` patch in
  `test_stream_bindings_usecases` is a caller-boundary mock (`restart_binding` reads
  `sensor_lifecycle._encoder_action` module-qualified through the facade) — unchanged. Full non-e2e green.

### D4 (encoder-admin chokepoint guard #18) — DROPPED, not viable
`/usr/local/bin/encoder-admin` is invoked from **8+ sites** (`mode_enforcer`, `recovery_executor`,
`system.py`, `application/camera/color_config.py`, `services/sensor_tuning_env.py`, the pre-existing
`services/encoder_admin.py`, and this port) — it is NOT chokepointed, so a #14-style "only from X"
guard would fail. Chokepointing it is a separate consolidation (the same theme as the P1 systemctl→
service-admin work). The existing `/usr/local/bin/*-admin`-must-be-sudo'd-on-one-line fitness guard
already covers this module (the new port satisfies it). The Phase 4 lock is the 4-1 facade-contract test.

### Note — two `encoder_admin.py` now exist (future de-dup, not this phase)
`services/encoder_admin.py` (`invoke`/`discover_units`, the admin-dashboard's C-04 adapter, `sudo -n`
+ subprocess) and `services/sensor_lifecycle/encoder_admin.py` (`_encoder_action`/`_encoder_status` +
readiness, `system.run`) are PARALLEL encoder-admin adapters — this duplication PRE-DATES the split
(both existed before; the split just gave sensor_lifecycle's version a module home). The implementations
differ (timeouts, family handling, error mapping); consolidating sensor_lifecycle's port onto
`services/encoder_admin.invoke` is a behavior-touching de-dup for a later cycle.
