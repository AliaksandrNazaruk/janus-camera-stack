# ARCHITECTURE_CURRENT — janus_camera_page (truth-map)

**Why this file exists:** the code moved faster than the prose. A reviewer reading older
design/baseline notes can conclude the route splits are "deferred" when they are *done*. This
is the single current-state anchor. If a doc disagrees with this file, this file wins (and that
doc should be fixed or archived).

Last reconciled: 2026-06-21, after D3 (application/service FastAPI de-leak CLOSED). D1+D2+D3 all done.
Companions: [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) ·
[LEGACY_COMPATIBILITY.md](LEGACY_COMPATIBILITY.md) ·
../SOURCE_OF_TRUTH.md ·
../PROJECT_FILE_MANIFEST.md

## The intended shape (and how far it's real)

```
routes/        HTTP boundary: auth + parse/validate + delegate
   ↓
application/   use-cases: orchestration, validation, response shaping (plain functions)
   ↓
services/      infra adapters: subprocess / CLI / file-IO / HTTP side effects
```

This is **partially** realized. Two distinct purity levels — don't conflate them:

| Level | Meaning | Status |
|---|---|---|
| **Route infra-purity** | `routes/**` contains no raw `subprocess` / `systemctl` / `httpx` | ✅ **CLOSED** (Phase 7, guard is unconditional) |
| **Use-case purity** | routes hold no orchestration; `application/` has no FastAPI; `services/` has no god-store; durable ops; centralized config | ✅ **CLOSED** — call-time config (D4) + durable ops (D5) + **`stream_bindings` route orchestration (D1)** + **`stream_binding_store` god-store split (D2)** + **application/service FastAPI de-leak (D3)** all done (Phases 9/11/12/12.3/13 + D3). Only LOW polish items (D6/D7/D8) remain. See KNOWN_LIMITATIONS |

So: the architecture is **no longer chaotic, and now structurally mature.** Honest score ~**9/10** —
the fat route (D1), the god-store (D2), and the application-FastAPI leak (D3) are all resolved; only
LOW polish items remain, plus the proxy adapters that are FastAPI-at-the-boundary **by design**.

## What is CLOSED (verified)

- **Route infra-purity** — `test_routes_have_no_subprocess_systemctl_httpx` (unconditional, no
  allowlist). Side effects live in `app/services/*`, orchestration in `app/application/*`.
- **admin_dashboard.py** 1213 → 336 (−72%) — C-04 Phases 1–4. Verticals: `systemd`,
  `encoder_admin`, `encoder_env`, `v4l2`, `janus_dashboard_admin`, `netinfo`, `soak_files`
  (services) + `services_admin`, `encoder_admin`, `provision_stream`, `device_inventory`,
  `mountpoint_admin`, `audit_view`, `dashboard` (application). See
  [design/ADMIN_DASHBOARD_SPLIT.md](design/ADMIN_DASHBOARD_SPLIT.md).
- **admin_config.py** 282 → 161 — Phase 5. systemctl → `services/systemd` (bare, distinct from
  the sudo path), apply/snapshot → `application/config_apply` + `config_view`.
- **depth.py** 270 → 100 — Phase 6. mux client → `services/depth_mux_client`, proxy + error
  mapping → `application/depth_mux_proxy`. Both depth paths preserved.
- **Import-time config (D4)** — Phase 9. `app/core/admin.py` now reads the token at **call time**
  via `admin_token()`; the import-time `ADMIN_TOKEN` constant is gone. ~11 test files migrated to
  construct the app *inside* the env-patch. Unblocked the config-order-sensitive test. (Also fixed
  the Phase-9 rate-limit-test root cause — a slow sync `/janus/restart` handler, logged as D8.)
- **`stream_bindings.py` use-case extraction (D1 — Phases 10/12, the first wave)** — finished in Phase 12.3 (see the CLOSED entry below).
  **888 → 748L**; the orchestration for **11 of 25 endpoints** moved into
  `application/stream_bindings/` — **10 FastAPI-free use-case files**: `restart_binding`,
  `stop_binding`, `set_fdir`, `remove_binding`, `ensure_janus`, `get_tuning`, `set_tuning`
  (Phase 10 small verticals) + `activate_local`, `activate_remote`, `delete_node` (Phase 12 heavy
  topology). Plain functions over commands → results/domain-errors; the route maps to HTTP. See
  [design/STREAM_BINDINGS_EXTRACTION.md](design/STREAM_BINDINGS_EXTRACTION.md) +
  [design/STREAM_BINDINGS_HEAVY_TOPOLOGY_EXTRACTION.md](design/STREAM_BINDINGS_HEAVY_TOPOLOGY_EXTRACTION.md).
  At this point 14 handlers were still inline (node CRUD, fleet, reconcile, firewall, binding create/list);
  **Phase 12.3 then moved them** — D1 route cleanup is CLOSED in the entry below.
- **Durable operation lifecycle (D5)** — Phase 11. The in-memory `_inflight` dict + bare daemon
  thread is replaced by `services/operation_journal.py` (flock'd, atomic JSON) +
  `services/node_operation_runner.py`: `run()` → durable status (running/succeeded/failed/
  interrupted), per-node **409** conflict, and `reap_orphans()` at startup (marks interrupted ops +
  un-sticks in-progress provision states). The route's `_spawn_node_op` is now a thin wrapper. See
  [design/OPERATION_JOURNAL.md](design/OPERATION_JOURNAL.md).
- **`stream_bindings.py` route cleanup (D1) — CLOSED** — Phases 10 / 12 / 12.3. The route is now a
  thin HTTP boundary (parse + auth + call use-case + map): **23 use-cases** in
  `application/stream_bindings/` cover per-binding ops, heavy topology (activate/delete), reads/lists,
  node lifecycle (check / maintenance / host-key / register / add), and create / fleet / firewall
  reconcile. The only orchestration left inline is `reconcile_janus_run_once` (deliberate — a one-call
  delegation to the reconcile_janus engine, a red-line) plus the residual provisioning glue
  (`_node_for_provision`, `_transport_for`, `NODE_BUNDLE_TAR`, `SSHTransport`, `capture_host_key`)
  feeding provision/activate/rotate via the durable runner — the next thinning candidate, tracked
  but non-structural (the fat-route debt itself is closed). See
  [design/STREAM_BINDINGS_ROUTE_CLEANUP.md](design/STREAM_BINDINGS_ROUTE_CLEANUP.md).
- **`stream_binding_store` god-store split (D2) — CLOSED** — Phase 13. The 892-line god-store is now a
  package of **7 responsibility modules** behind a pure re-export facade (`__init__.py`, zero logic):
  `models` (R1), `state_file` (R2+R3 persistence/corruption), `secrets` (R4 0600 tokens), `validation`
  (R10 LAN), `nodes` (R5 CRUD + the `remove_node` cross-entity cascade), `bindings` (R6–R9
  projection/read/write/allocation). The full **52-symbol public API** (`sbs.*`) is unchanged for all
  49 callers; class identity preserved. Extracted leaves-first (13A–13D) then the coupled core (13E1
  nodes, 13E2 bindings). See [design/STREAM_BINDING_STORE_SPLIT.md](design/STREAM_BINDING_STORE_SPLIT.md).

Campaign record: [design/ROUTE_PURITY_CLOSEOUT.md](design/ROUTE_PURITY_CLOSEOUT.md).

## What is OPEN (the next wave — debt, not chaos)

Pointers only; detail + evidence in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

- **Only LOW polish items remain:** `routes/admin_config.py` still imports services directly (D6);
  `routes/device_camera.py` not yet fully thin (D7); the slow sync `/janus/restart` handler (D8). None
  is structural.

(**D3 — application/service FastAPI de-leak — is CLOSED.** All 7 leaking domain modules now raise domain
errors mapped at the route; the only `HTTPException` users left are the 6 HTTP/WS-proxy adapters
(`depth_mux_proxy`, `proxy_base`, `depth_camera_proxy`, `janus_proxy`, `realsense_mux_proxy`, `ws_proxy`)
— FastAPI-at-the-boundary by design, locked by a fitness guard with an explicit allowlist. See
[design/APPLICATION_FASTAPI_DELEAK.md](design/APPLICATION_FASTAPI_DELEAK.md).)

## Things that are NOT debt (so reviewers stop re-flagging them)

See [LEGACY_COMPATIBILITY.md](LEGACY_COMPATIBILITY.md) for the evidence.

- **The HTTP `/depth*` routes are compatibility, not dead code.** Primary depth path is the
  Janus textroom round-trip; `/depth` is its fallback; `/depth/frame` + `/depth/frame_color_overlay`
  feed arm3d (external consumers: `xarm_service`, `frontend_service`). Do not delete.
- **Root `realsense_mux.py` is the hardware-free depth-contract fixture**, not a stale duplicate
  of the deployed mux. (Deployed mux = `host_infra/roles/encoder/files/realsense-mux.py`.)
- **`deploy/`, `host_infra/`, `infrastructure/` are deployment artifacts/templates**, not a second
  copy of the FastAPI app stack.
- **`camera_bringup/` is separable L0 tooling**, reached only via the `camera-admin` CLI (no imports).

## Planned next wave

`8 docs truth-map ✅ → 9 call-time config (D4) ✅ → 10 stream_bindings small verticals (D1) ✅ →
11 operation runner / journal (D5) ✅ → 12 heavy topology activate+delete (D1) ✅ →
12.3 finish route orchestration (D1) ✅ → 13 stream_binding_store split (D2) ✅ →
D3 application/service FastAPI de-leak ✅`.

**The architecture (layering) campaign (D1 route · D2 store · D3 FastAPI-leak) is complete.** All three
structural debts are closed, each behind a fitness guard.

**A SECOND campaign followed — production-risk hardening (Cycles 1–12, 2026-06).** It closed real failure
modes + residual structure: store fail-closed (#18), service-control boundary (#19), runtime-config
truth (#20), tracked tasks (#21), stream_bindings package (#22), the Janus NAT/TURN operation boundary
(#23), the admin-operation vocabulary (#24), settings ownership (#25), plus nat_config dead-code removal,
the desired-vs-actual state contract, and the D6 admin-routes review (found already-thin). **The fitness
guard count is now 25** (`#1`–`#25` in `tests/test_architecture_fitness.py`); D6 is CLOSED. **New here?
start at `docs/HANDOFF.md`**, then this file (the truth-map) + `AUDIT_CONTEXT.md` (the reviewer snapshot).
No phase starts without its own design note + GO (same discipline).
