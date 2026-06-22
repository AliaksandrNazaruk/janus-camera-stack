# State Baseline — 2026-06-20 (AFTER reconciliation)

> **Canonical baseline.** Supersedes the earlier `STATE_BASELINE_2026-06-20.md`
> (which described the *pre*-reconcile working tree and now lives only in `git stash`).

## TL;DR
The "architecture drift" seen in `camera_stack_2026-06-20.tar.gz` was a **working-tree
regression**, not missing architecture. HEAD already contained the advanced model; the
working tree had silently reverted a slice of it. The correct fix was a `git stash`
**surgical recovery**, NOT a schema refactor / new reconciler. Two real point-fixes then
landed on top.

## Git
- Branch: `refactor/consolidate-camera-stack` · HEAD: **`4a71044`**
  - `4a71044` sec(secrets): redact dev sudo password literal from tracked artifacts
  - `e24759d` fix(reconcile): honor operator-Stop — skip fdir-disabled bindings in reconcile_janus
  - `2cce717` sec(supply-chain): self-host all JS, drop CDN + fail-closed *(prior HEAD)*
- Tracked working tree **== HEAD** (clean). The reverted regression + the redundant
  session reconciler are preserved in `git stash`:
  - `stash@{0}` = the 35-file tracked regression
  - `stash@{1}` = `remote_mountpoint_reconciler.py` + its test + the old `STATE_BASELINE`
- Untracked (intentional — NOT part of the regression): `camera_bringup/`, `host_infra/`,
  `janus_camera_page/_archive/`, `Gateway Console Design System.zip`.

## Services (live)
| unit | state |
|---|---|
| `janus.service` | active (restarted 09:09 for secret rotation) |
| `janus-camera-page.service` | active, `:8900`, Type=notify, `healthz`/`readyz`=200 |
| `rs-stream@color.service` | active (cam10 RGB producer → RTP :5004) |
| `janus_camera_page_hook.service` | active (textroom datachannel hook, :9000) |
| `janus-turn-rotator.service` | inactive (timer-driven) |

## Routes
- `/api/v1/ui/*` mounted (`ui_viewmodel.router`); `/api/v1/ui/fleet` = 403 unauth (gated, correct).
- `/api/v1/admin/*` (nodes, stream-bindings, mountpoints, runtime-config, …). openapi lists `/api/v1/ui/fleet`.

## Node + bindings  (`/var/lib/camera-fdir/stream_bindings.json`)
- Node `node-084180e2ca81` — cam55, host `192.168.1.55`, serial `048522073892`, provision_state=`ready`.
- `048522073892:color` — **online**, mp 2000
- `048522073892:depth` — **online**, mp 2001
- `048522073892:ir1` — `configured_offline`, `fdir.enabled=false` (operator-stopped; deliberately NOT recreated)

## Janus mountpoints
- Local **static** (from `janus.plugin.streaming.jcfg`): 1305 rgb=cam10, 1306 depth, 1307 ir1, 1308 ir2.
- Remote **dynamic** (L4 `reconcile_janus`): 2000 color, 2001 depth.
- 2002 (ir1 remote): **ABSENT** — `reconcile_janus` skips fdir-disabled bindings (`e24759d`); validated live (`reconcile_janus: created=2 existing=0 failed=0 skipped=1`).
- cam10: stream_active, mp 1305, video_age ≈30–60 ms, mode nominal.

## Firewall (.10)
- Per-binding `ACCEPT udp from 192.168.1.55` on 5100–5105 + backstop `DROP 5100:5199`; default-DROP;
  Janus 8088/8188/7088 loopback-bound (8088 externally firewall-DROP'd). Unchanged this session;
  `.55` RTP confirmed passing (color/depth online).

## Secrets — ROTATED 2026-06-20 09:07
- Rotated: `janus_streaming_admin_key`, `cameras_secrets.{cam-rgb,cam-depth,cam-ir1,cam-ir2}`, `janus_textroom_secret`.
- 3 sync points kept consistent: `secrets.yml` (×2), `janus.plugin.streaming.jcfg`,
  `/etc/robot/camera-secrets.env` (`JANUS_STREAMING_ADMIN_KEY`). Validated: `failed=0` on reconcile.
- The textroom secret had **reused the dev sudo password** — now decoupled.
- Rollback backups `*.pre-rotate-20260620_090725` **deleted** after stability confirmed.
- Leaky rev-1 camera-stack archive replaced with secret-free build (sha `e0d3dd…`); only `secrets.yml.example` ships.
- Dev sudo password literal scrubbed from repo artifacts (`4a71044`); still in git **history**.
  The live **sudo password itself is unchanged** (owner stated it is temporary — rotate on their schedule).

## Regression guards (new — `tests/test_regression_guards.py`)
`test_rich_node_entry_contract` · `test_ui_viewmodel_router_mounted` ·
`test_reconcile_janus_wired_at_startup` · `test_no_runtime_cdn_in_csp_or_templates` ·
`test_secrets_file_gitignored_and_example_shipped` — each pins a contract the regression had broken.

## Process lesson (pre-review invariant)
Never run an architecture audit on a **dirty working tree** before classifying it:
intentional WIP vs accidental regression vs archive snapshot vs live deployment state. Before any
large review run, and only then analyze:
```
git status --short
git log --oneline -1
git diff --stat HEAD
pytest <targeted baseline>
curl /openapi.json   # route inventory
```

## Hardening completed since this baseline
- **#1 — release-archive gate** (`747deca`): `make release-archive` /
  `scripts/build_release_archive.sh` + `scripts/release_excludes.txt` — one-command,
  secret-free build that **fails closed** (deletes the artifact) if any secret slips in;
  `tests/test_release_archive.py`. Closes "gitignored ≠ excluded from tar" and the
  previously-half `test_no_real_secrets_in_archive` guard.
- **#2 — store fail-closed** (`63487bd`, was backlog item 4): a corrupt
  `stream_bindings.json` is quarantined (`.corrupt.<ts>`, original preserved); reads +
  mutations raise `StoreCorruptionError` (no silent-empty topology, no overwrite); a
  `store_corruption_status()` probe drives `readyz` → 503 `topology_store_corrupt`; a
  global handler maps it to a clean 503 on every route; `tests/test_store_corruption.py`.

### Live smoke — #2 loaded 2026-06-20 10:50 (L4-only restart)
`janus-camera-page.service` restarted alone — Janus (`09:09:30`) and `rs-stream@color`
(`07:20:10`) timestamps UNCHANGED, so the media plane was untouched. Post-check all green:
`healthz`/`readyz`=200, `/api/v1/ui/fleet`=403, cam10 `stream_active` mp 1305 (video_age 1 ms,
nominal), Janus mountpoints `[1305,1306,1307,1308,2000,2001]` (ir1 mp 2002 absent), `.55`
color/depth online, `reconcile_janus: created=0 existing=2 failed=0 skipped=1`, and **no**
StoreCorruptionError / topology_store_corrupt / traceback. #2 fail-closed protection is now
active in the live process.

- **Reconcile model** — **ADR** `docs/design/DESIRED_ACTUAL_RECONCILE_MODEL.md` (invariants R1–R9), plus:
  - read-only **`GET /api/v1/admin/reconcile/drift`** (`services/reconcile_drift.py`, `6000476`) —
    classifies stored remote bindings vs live Janus mountpoints/RTP (in_sync / missing / stale /
    stopped_by_operator / unexpected); no mutations.
  - write **`POST /api/v1/admin/reconcile/janus/run-once`** (`binding_provision.run_janus_reconcile_once`, `f942a0e`) —
    idempotent; creates MISSING mountpoints for ACTIVE remote bindings only (skip predicate shared with
    the drift report); no restart/firewall/destroy/provision; returns before/after drift.

### Live smoke — drift endpoint loaded 2026-06-20 11:45 (L4-only restart)
`janus-camera-page.service` restarted alone (L4 10:50:39 → **11:45:02**; Janus `09:09:30` and
`rs-stream@color` `07:20:10` UNCHANGED — media plane untouched). All 11 facts green:
`healthz`/`readyz`=200, `drift_unauth`=403, cam10 `stream_active` mp 1305 (video_age 37 ms),
Janus mountpoints `[1305,1306,1307,1308,2000,2001]`. Authenticated
`GET /api/v1/admin/reconcile/drift` (200) returned **`drift=false`**, counts
`{in_sync: 2, stopped_by_operator: 1}` — color (mp 2000, 4 ms) + depth (mp 2001, 7 ms) `in_sync`,
ir1 (mp 2002 absent) `stopped_by_operator`, `unexpected_mountpoints=[]`. Journal clean
(only `reconcile_janus created=0 existing=2 failed=0 skipped=1`). The read-only desired/actual
drift diagnostic is now live.

### Live smoke — run-once endpoint loaded 2026-06-20 12:07 (L4-only restart)
`janus-camera-page.service` restarted alone (L4 11:45:02 → **12:07:41**; Janus `09:09:30` and
`rs-stream@color` `07:20:10` UNCHANGED). All 11 facts green: `healthz`/`readyz`=200;
`drift_unauth`=403, `runonce_unauth`=403; Janus mountpoints `[1305,1306,1307,1308,2000,2001]`
(unchanged). Authenticated `GET …/drift` = `drift=false` (color/depth `in_sync`, ir1
`stopped_by_operator`). Authenticated `POST …/reconcile/janus/run-once` = a verified **NO-OP**:
`result {created:0, existing:2, skipped:1, failed:0}`, before/after `drift=false`, `outcomes={}`.
Journal clean. The explicit run-once Janus reconcile (write counterpart) is now live and
correctly does nothing on a healthy fleet.

## Deferred architecture backlog (controlled hardening — NOT emergency)
1. `admin_dashboard.py`: split route + orchestration + subprocess into application use-cases + infra adapters.
2. Explicit desired/actual reconcile model — **ADR written** (`docs/design/DESIRED_ACTUAL_RECONCILE_MODEL.md`, invariants R1–R9); implementation (unified pass + `drift` diagnostic) deferred.
3. Single operator console; retire legacy console ambiguity.
4. ~~Store corruption → fail-closed / quarantine~~ — **DONE** (`63487bd`).
5. provision / delete-node → operation journal (transactional).
6. Extend the regression guards as contracts evolve.
