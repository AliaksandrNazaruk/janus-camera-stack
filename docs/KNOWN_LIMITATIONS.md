# KNOWN_LIMITATIONS — janus_camera_page

The architectural debt left *after* route infra-purity closed (Phase 7). Route infra-purity is
**one** layer of cleanup, not the whole architecture. With **D1 (route), D2 (store), AND D3
(FastAPI-leak) all closed**, the honest score is ~**9/10**; only LOW polish items (D6/D7/D8) remain.
Companion: [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md).

Last reconciled: 2026-06-21, after D3 (application/service FastAPI de-leak CLOSED).

## The gap in one line

```
CLOSED:  routes/** no subprocess/systemctl/httpx (Phase 7) · call-time config (Phase 9)
         durable operations (Phase 11) · routes/stream_bindings.py orchestration → 23 use-cases (D1)
         services/stream_binding_store god-store → 7-module package behind a facade (Phase 13, D2)
         application/+services/ FastAPI de-leak → 7 domain modules raise domain errors (D3)
OPEN:    LOW polish only: D6/D7/D8
```

## Debt items (evidence-backed, ranked)

| # | Item | Evidence / status | Severity | Phase |
|---|---|---|---|---|
| ~~D1~~ | ~~**`routes/stream_bindings.py` is a fat route**~~ — ✅ **CLOSED (Phases 10/12/12.3)** | 888 → **711L**. `_inflight` dict + bare daemon thread **removed** → `_spawn_node_op` thin wrapper over `node_operation_runner.run`. Route orchestration extracted to **23 FastAPI-free use-cases** in `application/stream_bindings/` (per-binding · heavy topology · reads/lists · node lifecycle · create/fleet/firewall). Only `reconcile_janus_run_once` stays inline (deliberate — one-call delegation to the reconcile_janus engine, a red-line) + the shared transport/host-key helpers feeding provision/activate/rotate via the runner. See [design/STREAM_BINDINGS_ROUTE_CLEANUP.md](design/STREAM_BINDINGS_ROUTE_CLEANUP.md). | — | ✅ **10/12/12.3** |
| ~~D2~~ | ~~**`services/stream_binding_store.py` is a god-store**~~ — ✅ **CLOSED (Phase 13)** | The 892-line module is now a **package of 7 responsibility modules** behind a pure re-export facade (`__init__.py`, zero logic): `models` (R1) · `state_file` (R2+R3) · `secrets` (R4) · `validation` (R10) · `nodes` (R5, incl. the `remove_node` cross-entity cascade) · `bindings` (R6–R9). Full **52-symbol public API** unchanged for all 49 callers; class identity preserved; moved verbatim. Leaves-first (13A–13D) then the coupled core (13E1 nodes / 13E2 bindings). See [design/STREAM_BINDING_STORE_SPLIT.md](design/STREAM_BINDING_STORE_SPLIT.md). | — | ✅ **13** |
| ~~D3~~ | ~~**FastAPI leaks `application/` + `services/`**~~ — ✅ **CLOSED (D3.1–D3.4)** | All **7 domain modules** de-leaked (raise domain errors; routes map byte-identical): `encoder_admin` · `services_admin` · `config_apply` (list[str] detail) · `soak_files` · `encoder_env` · `mountpoint_admin` · `provision_stream`. The only remaining `HTTPException` users are the **6 HTTP/WS-proxy adapters** (`depth_mux_proxy`, `proxy_base`, `depth_camera_proxy`, `janus_proxy`, `realsense_mux_proxy`, `ws_proxy`) — accepted exceptions (take `Request`, return `Response` — the HTTP boundary itself), locked by a fitness guard with an explicit allowlist. See [design/APPLICATION_FASTAPI_DELEAK.md](design/APPLICATION_FASTAPI_DELEAK.md). | — | ✅ **D3** |
| D4 | ~~**Import-time config**~~ — env read at module import, not call time | ✅ **CLOSED (Phase 9).** `app/core/admin.py` reads via call-time `admin_token()`; the import-time `ADMIN_TOKEN` constant is gone; ~11 test files build the app inside the env-patch. Design: [design/TRACK_A_SETTINGS_RELOCATION.md](design/TRACK_A_SETTINGS_RELOCATION.md) | — | ✅ **9** |
| D5 | ~~**Non-durable operation lifecycle**~~ — long node ops in-memory only | ✅ **CLOSED (Phase 11).** `services/operation_journal.py` (flock'd, atomic JSON) + `services/node_operation_runner.py`: `operation_id` (uuid4), durable status (running/succeeded/failed/interrupted), per-node **409** conflict, persisted `last_error`, `reap_orphans()` at startup (un-sticks in-progress provision states). Design: [design/OPERATION_JOURNAL.md](design/OPERATION_JOURNAL.md) | — | ✅ **11** |
| D6 | `routes/admin_config.py` still imports services directly (`jcfg_renderer, public_ip, secret_store, audit`) | post-Phase-5: route is short, no subprocess/systemctl | **LOW** — do not re-touch now | — |
| D7 | `routes/device_camera.py` not yet fully thin | — | **LOW** — after stream_bindings | — |
| D8 | **Slow sync admin handlers** block a threadpool worker on a subprocess | `/janus/restart` (`routes/janus.py:208`, sync `def`) shells out to `janus-admin` (~30–90 s on failure); ties up a worker and interacts badly with the 5/min admin limiter (a slow caller never trips the limit — Phase-9 rate-limit-test finding). Phase 11's runner is for *node* ops; this handler is unchanged. | **LOW/MED** | with async-ops follow-up |
| ~~D9~~ | ~~`remove` Janus teardown is dead~~ — **RETRACTED: false alarm (2026-06-20)** | Misread in Phase 10.3. `janus_admin.destroy_mountpoint` is **`@_with_handle`-decorated** — it creates the session + attaches the streaming handle internally, so calling it with only `mp_id`/`mp_secret` is **correct** (verified: the decorator injects session_id/handle_id). It "fails silently" only in tests (no Janus → swallowed best-effort); in production it destroys the mountpoint. No bug. | — | none — nothing to fix |

## Ordered plan (and why this order)

1. ✅ **Phase 9 — call-time config fix (D4).** Done. `admin_token()` accessor; config-order test unblocked.
2. ✅ **Phase 10 — `stream_bindings.py` small verticals (D1).** Done. restart/stop/fdir/remove/
   ensure-janus/tuning ×2 → `application/stream_bindings/`. Characterize → move verbatim → re-point.
3. ✅ **Phase 11 — operation runner (D5).** Done. `_inflight` dict + daemon thread → `OperationJournal`
   + `NodeOperationRunner` (durable status, 409, reap on startup).
4. ✅ **Phase 12 — heavy topology (D1).** Done. `activate_local`/`activate_remote` (12.1) +
   `delete_node` synchronous cascade (12.2) → `application/stream_bindings/`.
5. ✅ **Phase 12.3 — finish route orchestration (D1).** Done. **12.3A** read/list/view; **12.3B**
   node check / maintenance / host-key; **12.3C** create binding / fleet reconcile / firewall
   reconcile; **12.3D** register / add-by-host. `reconcile_janus_run_once` left inline by decision
   (red-line). HTTP-boundary helpers (`_node_out`, `_binding_out`, `_require_lan_ipv4`,
   `_transport_for`, `_node_for_provision`, `_spawn_node_op`) kept in the route.
6. ✅ **Phase 13 — `stream_binding_store.py` split (D2).** Done. Facade-first, leaves-first: package
   with an `__init__` re-export facade → 13A `models` → 13B `state_file` → 13C `secrets` → 13D
   `validation` → 13E1 `nodes` → 13E2 `bindings`. Full suite green at every step; 52-symbol API
   unchanged. See [design/STREAM_BINDING_STORE_SPLIT.md](design/STREAM_BINDING_STORE_SPLIT.md).
7. ✅ **D3 — application/service FastAPI de-leak.** Done (D3.1 `encoder_admin` → D3.2A `services_admin`
   → D3.2B `config_apply` → D3.3A `soak_files` → D3.3C the `encoder_env`/`mountpoint_admin`/
   `provision_stream` cluster → D3.4 closeout). The 7 domain modules raise domain errors; the routes
   map them. The 6 HTTP/WS-proxy adapters are documented accepted exceptions, locked by a fitness guard.
8. ⏭ **Remaining — LOW polish (D6/D7/D8)** as convenient. None is structural; each needs its own
   design note + GO.

## Sequencing red line (satisfied; both structural debts closed)

The rule was: **do NOT split the store before the route callers are thin.** That precondition was met
by Phase 12.3 (D1), and Phase 13 then closed D2 facade-first so the 52-symbol public API never broke.
Both structural debts (fat route, god-store) are now resolved; the remaining work (D3 + LOW items) is
local and non-cross-cutting — each still starts with its own design note + GO (same discipline).

## Discipline (same as the route-purity campaign)

Characterization tests first → move verbatim → re-point tests with identical assertions →
one commit per phase → design-note approval before starting → no behavior change. Red-line
zones stay untouched unless explicitly in scope: reconcile_janus, firewall, systemd, Janus
control plane, the depth `/depth*` compatibility surface.
