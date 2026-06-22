# SETTINGS_OWNERSHIP — Cycle 9 / G5 recon (GATED, no code yet)

G5 = settings/ports centralization. The recon's framing (per the steer): NOT "where is there a lot of
config" but **"where does settings ownership affect runtime correctness."** Most module-level
`os.getenv` is fine — deployment-time tunables read once at import. The recon finds the FEW genuine
ownership violations. No code until GO.

## Inventory (verified 2026-06-21)
- `os.getenv`/`os.environ`: routes **2**, application **0**, services **~60**, core **~47** (42 in
  `settings.py` — the legitimate owner), config **1**.
- `app/config` already centralizes the network constants (`PORTS`, `DEVICES` via `network_defaults`) —
  a good precedent; not in scope.
- Guard **#3** already bans `os.getenv` in `routes/` (services are allowed module-level env constants —
  encapsulated internal config, per CONTRACT.md).

## Classification (the steer's A–F)
- **A — true ownership violation (the target).** Same env var read in BOTH `settings.py` (as a `Settings`
  field) AND raw via `os.getenv` in a service → two sources of truth.
  - **`fps_profile_path` — REAL correctness risk.** `thermal.py:35` reads its OWN frozen
    `FPS_PROFILE_PATH = os.getenv(...)` and **WRITES** the profile there (`thermal.py:67`); `mode_enforcer`
    **READS** the path from `settings.fps_profile_path` (`:204,:224`). The fps-profile file is a
    coordination channel thermal→mode_enforcer, and its path has SPLIT ownership. Defaults agree in prod
    (both `/run/camera/fps_profile`), so it's LATENT — but a test/env override of one source (or any
    future default drift) makes the writer and reader use DIFFERENT files → the handoff silently breaks.
    This is the one genuine "ownership affects runtime correctness" finding.
- **A (lesser) — dead redundant settings fields.** `settings.ws_max_connections` / `ws_msg_rate_per_sec`
  exist but are read NOWHERE; `ws_proxy` uses its own `_MAX_WS_CONNECTIONS` / `_WS_MSG_RATE_PER_SEC`
  (the de-facto owner). Redundancy, no correctness impact — the Settings fields are dead.
- **A — dead captured secret.** `NODE_AGENT_TOKEN = os.getenv(...)` at import in `node_client.py:30` +
  `node_provisioner.py:40`, but UNUSED (review-H1 removed the global fallback; node_client uses the
  per-node secret-store token). A vestigial import-time secret capture → remove.
- **B/C — harmless deployment tunables/constants (~50, LEAVE).** thermal thresholds, rate-limit tokens,
  watchdog windows, lock paths/timeouts, plugin/revision dirs, ws limits, jcfg lock, recovery dedup,
  adapter deployment config (node SSH/bundle, janus http/timeout, secret-store paths). Read once at
  import; they're deployment config that does NOT change at runtime → import-time capture is CORRECT.
  Moving these into a settings god-object is the "перенести всё ради красоты" churn to avoid.
- **D — test override seams.** `BIND_STATE_PATH` / `ALLOC_STATE_PATH` etc. are deliberate monkeypatch
  anchors (handled in prior cycles); not touched.
- **F — runtime-config values frozen statically.** The Cycle-3 frozen-literal class (`ice_policy` /
  `turn_credential_ttl_seconds`) is ALREADY relocated to `rs-runtime.env` (Track A). No remaining
  runtime-config field is duplicated by a static import-time read (verified — the runtime-config schema
  values live in rs-runtime.env, not re-`getenv`'d in services).

## The minimal correctness cut (D1 — gate)
- **(A) Fix `fps_profile_path` ownership only [LEAN].** Make `thermal` read the path from
  `settings.fps_profile_path` at call time (like `mode_enforcer` already does) instead of its frozen
  `os.getenv` const → ONE source of truth; writer and reader always agree; test-overridable. Tiny,
  correctness-relevant, low risk. Optionally also (cleanup, same cut): drop the two DEAD ws settings
  fields so `ws_proxy` is the sole owner. Guard **#25**: no env var owned by `settings.py` is ALSO read
  raw via `os.getenv` in services (locks the split-ownership ban; intersection must be empty).
- **(B) Broad centralization** — move the ~50 service constants into `Settings`. Reject: churn for no
  correctness gain; breaks the services-own-their-deployment-config pattern; the steer's "god object" trap.
- **(C) Doc-only** — record the violation, change nothing. Cheapest but leaves the latent fps split.

## Plan — assuming (A), sub-commits (tests-first)
- **G5A (this)** — recon + this design note.
- **G5B** — `thermal` reads `get_settings().fps_profile_path` (call-time); a characterization test that
  overriding `settings.fps_profile_path` redirects BOTH the thermal write and the mode_enforcer read to
  the same file (today it doesn't — RED→GREEN). Drop the dead ws settings fields. Guard **#25**
  (settings-owned env not re-read raw in services). Remove the dead `NODE_AGENT_TOKEN` globals (separate
  small commit if it touches more). Suite green.

## Open decisions to gate (GO before any code)
- **D1 — (A) fps fix + guard / (B) broad / (C) doc-only.** Lean **(A)**.
- **D2 — guard #25 scope:** ban settings-owned env re-read raw in `services/` (+ `application/`), with the
  legit deployment-constant set NOT counted (they're not in settings). Lean: the precise intersection ban.
- **D3 — fold in the `NODE_AGENT_TOKEN` dead-global removal,** or its own follow-up. Lean: same cycle if
  trivially dead; verify no remaining reader first.

## Status — DONE (2026-06-21), scope (A)
Decisions: **D1=(A)** fps fix + dead-field cleanup + guard; **D3** removed the dead `NODE_AGENT_TOKEN`
globals in the same cycle.
- **G5A** — this recon + design note.
- **G5B** (`<this commit>`):
  - `thermal.set_fps_profile` now writes to `get_settings().fps_profile_path` (call-time) instead of a
    frozen `os.getenv` const → ONE owner (settings); the thermal writer and the mode_enforcer writer can
    no longer diverge on the coordination-file path. The build found this is even sharper than the recon:
    BOTH thermal AND mode_enforcer *write* the fps-profile signal (the external pipeline reads it), so a
    path split silently drops one writer's output. `test_thermal` re-pointed (override
    `settings.fps_profile_path` → the thermal write follows it; was a frozen-const patch).
  - Removed the DEAD `Settings.ws_max_connections` / `ws_msg_rate_per_sec` (ws_proxy owns them) and the
    DEAD `NODE_AGENT_TOKEN` import-time captures in `node_client` + `node_provisioner` (unused since
    review-H1's per-node-token move).
  - Guard **#25** `test_settings_owned_env_not_reread_raw_in_services`: no env var is read in BOTH
    `settings.py` and raw in `services/`. The raw intersection is now exactly `{JANUS_MOUNT_ID}` — the
    one DOCUMENTED leaf-store decoupling (`stream_binding_store` reads it raw to import without the
    settings stack) — allowlisted. **25 fitness guards.**
- **Explicitly LEFT (per the steer):** the ~50 service deployment tunables/constants stay module-level
  (import-time capture is correct for deployment config); no settings god-object; runtime-config
  (rs-runtime.env) stays separate from static settings.

## Red lines (incl. the steer)
No "one settings god object". Don't move deployment constants for symmetry. Don't break the test
monkeypatch seams without a characterization test. Don't ban `os.getenv` globally without the
deployment-constant allowance (guard #3 already scopes routes; #25 bans only the DUPLICATE-ownership
case). Don't touch host_infra / FDIR. Keep runtime-config (rs-runtime.env) and static deployment config
separate. Tests-first; full non-e2e suite green per sub-commit.
