# Route-purity closeout — drain `_ROUTE_INFRA_BASELINE` to empty

## Why

C-04 (`ADMIN_DASHBOARD_SPLIT.md`) closed `admin_dashboard.py` and added the fitness guard
`test_routes_have_no_subprocess_systemctl_httpx`: **`app/routes/**` must not import
`subprocess`/`httpx` or carry a `"systemctl"` command literal.** Two pre-existing offenders
were allowlisted so the debt was *named, not hidden*:

```
_ROUTE_INFRA_BASELINE = {"depth.py", "admin_config.py"}
```

The boundary is a *convention with exceptions* today. The goal of this campaign is to make it
an **enforced invariant**:

```
routes/**: no subprocess, no systemctl, no httpx
exceptions: none
```

Done in small, behavior-preserving phases — one route file at a time, each ending with that
file removed from the baseline (the guard proves it).

## Order (riskiest first)

1. **Phase 5 — `admin_config.py`** — owns `systemctl restart janus/relay`, the most dangerous
   zone (control plane). Do it first, carefully.
2. **Phase 6 — `depth.py`** — httpx mux client (depth proxy). Lower blast radius.
3. **Phase 7 — tighten the guard** — once the set is empty, delete `_ROUTE_INFRA_BASELINE`
   and make the guard unconditional. Boundary becomes an invariant.

## Critical finding (drives Phase 5 design)

There are **three** distinct systemctl/service-active contracts in the tree — do **not** unify
them as part of a move:

| caller | command | returns |
|---|---|---|
| `services/systemd.py` (dashboard, Phase 1) | `sudo -n /bin/systemctl restart <unit>` | `(rc, stderr)`, raises on exec fail |
| `admin_config._systemctl` | **bare** `systemctl <action> <unit>` (no sudo) | `bool`, swallows exc → `False` |
| `mode_enforcer._is_service_active` | (its own) | `bool` |

The divergence is deliberate and tested: `test_boundary_fitness.test_no_raw_sudo_systemctl_in_production`
forbids `"sudo","systemctl"` *adjacent*; systemd.py dodges via `-n /bin/systemctl`, admin_config
dodges by going bare (the unit runs with systemctl rights — see `override.conf`).

**Decision (user-gated 2026-06-20): PRESERVE bare-no-sudo verbatim.** Phase 5 is a pure
relocation; unifying the sudo discrepancy is a separate, explicitly-gated decision for later.
mode_enforcer is out of scope entirely.

## Phase 5 — `admin_config.py` (scope)

| Move | Target | Note |
|---|---|---|
| `_systemctl(action,unit,timeout)->bool`, `_service_active(unit)->bool` | `services/systemd.py` (extend) | **bare** `["systemctl",...]`, bool, swallow — verbatim, kept DISTINCT from the sudo'd `restart_unit` |
| `apply_config` body (render→janus→relay→audit→`ApplyResponse`) | `application/config_apply.py` (new) | `ApplyResponse` model moves here |
| `get_snapshot` body (secrets+paths+nat+active probes+humanize) | `application/config_view.py` (new) | `ConfigSnapshot`,`SecretSnapshot`,`_humanize_age` move here |
| `@router.*` handlers | stay in `admin_config.py`, now THIN | drop `import subprocess` |

Routes left as-is (already free of infra primitives — thin secret_store/jcfg/public_ip calls):
`reveal_secret`, `rotate_secret`, `set_field`, `detect_public_ip`, `set_nat_mapping`.

**Acceptance:** same paths/methods/auth/rate-limit/response shapes; restart command stays bare
`systemctl restart janus.service ‖ janus` (no sudo, no `/bin/` path added); `.service‖bare-name`
fallback + bool/swallow + timeouts (30/15/3) unchanged; apply ordering + partial-failure error
strings + audit events unchanged; `admin_config.py` removed from `_ROUTE_INFRA_BASELINE` and the
guard passes; `test_boundary_fitness` / `test_audit_log` / oracle green.

**Red lines:** no unifying the three systemctl paths; no sudo added to admin_config's restart;
no touching mode_enforcer / recovery_ladder / reconcile / secret_store rotation / jcfg_renderer;
no new endpoints / auth / rate-limit changes. Characterization-first, then move verbatim, then
re-point with identical assertions.

## Phases

5. ✅ **DONE** — `admin_config.py`. `_systemctl`/`_service_active` → `services/systemd.py`
   (`systemctl_action`/`is_active`, **bare, no sudo, verbatim**; kept distinct from the sudo'd
   `restart_unit`, asserted by a test). `apply_config` → `application/config_apply.py`
   (`ApplyResponse` moves here); `get_snapshot` → `application/config_view.py`
   (`ConfigSnapshot`/`SecretSnapshot` + `_humanize_age` move here). Routes thinned to delegates,
   `import subprocess` dropped — **282→161 lines (−43%)**. `admin_config.py` removed from
   `_ROUTE_INFRA_BASELINE` (now `{depth.py}`); the guard proves it clean. Characterized first,
   re-pointed with identical assertions; restart command/ordering/fallback/error-strings/audit
   unchanged. The sudo-vs-bare discrepancy was **preserved** (not unified) per the gated decision.
6. ✅ **DONE** — `depth.py`. Mux `httpx.AsyncClient` (lazy lifecycle, verbatim) →
   `services/depth_mux_client.py` (`get_client`/`close`); proxy + httpx→HTTPException error
   mapping + `_inc_depth_proxy_errors` + `DepthResponse` → `application/depth_mux_proxy.py`
   (`proxy_realsense`/`depth_at`/`frame_color_overlay`/`depth_map_load`, returning FastAPI
   responses). Routes thinned to delegates (param-parse/422 stays), **270→100 lines**, no `httpx`.
   Shutdown hook re-pointed (`core/events` → `depth_mux_client.close`). Existing
   `test_depth_routes.py` re-pointed (`app.routes.depth._get_mux_client` →
   `app.services.depth_mux_client.get_client`), same assertions. All routes preserved verbatim
   (both depth paths intact); per-route status codes + detail strings unchanged. **Baseline
   drained to `set()`** — guard effectively unconditional.
7. ✅ **DONE** — deleted `_ROUTE_INFRA_BASELINE` + its skip; `test_routes_have_no_subprocess_
   systemctl_httpx` is now **unconditional** (no allowlist — a new infra primitive in `routes/`
   is always a failure). **Campaign closed.**

## Baseline burn-down — CLOSED

```
C-04 Phase 4 (guard added): {depth.py, admin_config.py}
Phase 5 (admin_config):     {depth.py}
Phase 6 (depth):            {}
Phase 7:                    baseline mechanism deleted → guard UNCONDITIONAL ✅
```

`routes/**` is now an enforced invariant: no `subprocess`, no `systemctl`, no `httpx`, no
exceptions. Side effects live in `app/services/*` adapters, orchestration in `app/application/*`.

### Phase 6 decision (user-gated)

Recon proved depth.py is **dual-path by design, not dead**: the primary click-to-depth path is
now the Janus **textroom** round-trip (browser → textroom_relay → mux:8000/depth_query → SSE
`/depth_events`), which **bypasses depth.py entirely**. depth.py's HTTP routes remain a live
**fallback** (`GET /depth` when the textroom backchannel isn't ready) **and the arm3d 3-D frame
source** (`/depth/frame`, `/depth/frame_color_overlay`). `/depth/color_frame` has zero callers
(dead); `/depth_map/load` is test-only.

**Decision:** depth-by-mux is a kept first-class capability ("get depth via textroom OR mux —
frontend or backend"). So **Option 1 — refactor-preserve ALL routes verbatim** (no pruning, no
deletion, no behavior change); just move httpx out of the route to drain the baseline. The dead/
test-only routes are carried along unchanged (pruning them is a separate, externally-verified
decision, not part of route-purity). The use-case returns FastAPI `Response`/`JSONResponse`
directly (exact passthrough; application/ may depend on fastapi.responses — already precedented).
Out of scope / untouched: textroom_relay, depth_events (SSE), depth_proxy (L5 cross-node),
depth_camera_proxy, proxy_base, realsense-mux.

## Phase 6 plan — `depth.py` (the depth/realsense_mux proxy)

`depth.py` proxies to the local `realsense_mux` (`REALSENSE_MUX_URL`, :8000) via a hand-rolled,
lazily-initialized `httpx.AsyncClient`. The guard forbids `httpx` in `routes/**` — and the
`except httpx.*` error-mapping blocks reference `httpx`, so **the error mapping must move out
with the client** (can't just relocate the client and keep the catches in the route).

| Move | Target | Note |
|---|---|---|
| `_mux_client`, `_mux_client_lock`, `_get_mux_client`, `close_mux_client` | `services/depth_mux_client.py` (new) | **verbatim** lazy init + double-checked lock + `Timeout(connect=2,read=5,write=2,pool=5)`. Exposed as `get_client()` / `close()` |
| `_proxy_realsense`, `get_depth` core, `frame_color_overlay`, `depth_map_load` orchestration + the `httpx.*→HTTPException` mapping + `_inc_depth_proxy_errors` + `DepthResponse` | `application/depth_mux_proxy.py` (new) | per-route error **detail strings + status codes preserved byte-for-byte** |
| `@router.*` handlers | stay in `depth.py`, now THIN | param parse (x/y/message/clamp/422) stays (HTTP input); NO `httpx` import |
| `events.py:190` shutdown close | re-point import → `services/depth_mux_client.close` | lifecycle hook unchanged, just relocated |
| `test_depth_routes.py` patches (`app.routes.depth._get_mux_client` ×11) | re-point → `app.services.depth_mux_client.get_client` | existing oracle; same assertions |

**Preserve exactly (the landmines):**
- Per-route mappings differ and must NOT be homogenized: `_proxy_realsense` catches
  `Timeout→ConnectError→HTTPError` (502 "unreachable" vs 502 "proxy error"); `get_depth`,
  `frame_color_overlay`, `depth_map_load` catch `Timeout→HTTPError` only. Detail strings differ
  per route ("Depth query timeout" vs "Aligned RGBD timeout" vs "Depth map timeout" …).
- Lazy init + double-checked lock; `Timeout(connect=2,read=5,write=2,pool=5)`; **no `Limits` added**.
- `depth_map_load` camera_type branch (depth → mux; color → existing `services/depth_camera_proxy.get`).
- Metric `depth_proxy_errors_total` increments at the same points.

**Decision — do NOT adopt `AsyncHttpProxy`/`proxy_base` for the mux client.** `depth_camera_proxy`
uses it (start/stop lifecycle + connection `Limits`); the mux client is lazy + no limits.
Converting would change lifecycle + add pooling limits — a behavior change. Preserve the lazy
client verbatim; unifying onto `proxy_base` is a separate, gated decision (cf. the Phase-5 sudo call).

**Acceptance:** httpx fully gone from `depth.py`; route paths/methods/response shapes/viewer-auth/
rate-limit unchanged; timeout + error mapping (codes + strings) unchanged; client lifecycle
(lazy init, shutdown close) unchanged; existing `test_depth_routes.py` green after re-point;
`depth.py` removed from `_ROUTE_INFRA_BASELINE` → **`{}`**; guard passes.

**Red lines:** no timeout/limits/lifecycle change; no `proxy_base` conversion; no touching
`depth_camera_proxy` / `proxy_base` / `realsense_mux` / `depth_events`; one phase (the guard forces
client + error-mapping to move together, so 6A/6B can't cleanly split).

## Baseline burn-down

```
C-04 Phase 4 (guard added): {depth.py, admin_config.py}
Phase 5 (admin_config):     {depth.py}
Phase 6 (depth):            {}            ← goal
Phase 7: delete _ROUTE_INFRA_BASELINE, guard unconditional
```
