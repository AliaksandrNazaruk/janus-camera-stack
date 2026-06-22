# APPLICATION_FASTAPI_DELEAK — D3 recon + plan

The `application/` layer is supposed to be FastAPI-free (domain results + domain errors; the route
maps to HTTP). It mostly is — the 23 `application/stream_bindings/*` use-cases are the reference.
But a set of older C-04 use-cases still `raise HTTPException`, and recon shows the leak also reaches
the **service** layer. This note is the recon + plan. **No code until approved.** Companions:
[STREAM_BINDINGS_ROUTE_CLEANUP.md](STREAM_BINDINGS_ROUTE_CLEANUP.md) (the clean pattern) ·
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) D3.

## 1. Current leaks (the COMPLETE map — 9 modules, not 6)

Recon (`grep -rn 'raise HTTPException' app/application app/services`) found the documented 6 app
use-cases **plus 3 service modules** — the leak is deeper than the truth-map recorded.

| Module | Layer | Raises | Status codes | Detail | Route caller | Notes |
|---|---|---|---|---|---|---|
| `encoder_admin.py` | app | 3 | 400 · 400 · 500 | str | `admin_dashboard` | clean — validate + exec |
| `services_admin.py` | app | 4 | 400 · 400 · 500 · 500 | str | `admin_dashboard` | clean — refuse-self / allowlist / exec |
| `config_apply.py` | app | 1 | 500 | **`list[str]`** | `admin_config` | detail is a LIST (structured) |
| `mountpoint_admin.py` | app | 7 | 500 · 502 · 400 · 502 · 502 | str | `admin_dashboard` | admin-key / janus attach / info |
| `provision_stream.py` | app | 0 (catches) | — | — | `admin_dashboard` | `except HTTPException: raise` — catches `encoder_env` |
| `depth_mux_proxy.py` | app | 19 | upstream-passthrough + 502/504 | str / passthrough | `depth` | **OUTLIER** — also *returns* `JSONResponse`/`Response` |
| `encoder_env.py` | **svc** | 1 | 400 | str | (via provision_stream + dashboard) | `write_env_files` invalid-instance |
| `soak_files.py` | **svc** | 2 | 400 · 404 | str | `admin_dashboard` | filename validation / not-found |
| `proxy_base.py` | **svc** | 4 | 503 · 504 · 502 · 502 | str | (base class for L5 proxies) | not-ready / timeout / unreachable / error |

Per-raise inventory of the cleaner ones (exact status + detail, for the route to reproduce
byte-identically):

- **encoder_admin**: `validate_encoder_target` → 400 `Unknown family …` · 400 `Family … requires
  alphanumeric instance …`; `encoder_action` → 500 `encoder-admin exec failed: {exc}`.
- **services_admin.restart_service**: 400 `Refusing to restart self …` · 400 `Service {…!r} not in
  restartable allowlist: …` · 500 `unknown method: {method}` · 500 `restart exec failed: {exc}`.
- **config_apply.apply**: 500 `detail=errors` where `errors: list[str]` (NOT a string — the domain
  error must carry the list verbatim).
- **mountpoint_admin**: `create_mountpoint`/`destroy_mountpoint` → 500 `STREAMING_ADMIN_KEY not set …`
  · 502 `{err}` (attach); `mountpoint_info` → 400 `mp_id must be 1-65535` · 502 `janus unreachable:
  {exc}` · 502 `janus returned unexpected structure`.
- **encoder_env.write_env_files**: 400 `Invalid instance name {instance!r}`.
- **soak_files**: 400 `invalid filename` · 404 `not found`.
- **proxy_base** (base for `depth_camera_proxy` etc.): 503 `{name} proxy client not ready` · 504
  `{name} proxy timeout` · 502 `{name} unreachable` · 502 `{str(exc)}`.

## 2. The existing clean pattern (the target)

`application/stream_bindings/*` (Phases 10–12.3) is the reference:

```python
# application/ — no FastAPI
class BindingNotFound(Exception): ...          # domain error in results.py
def remove_binding(cmd) -> RemoveBindingResult:
    if ...: raise BindingNotFound(binding_id)  # raise a DOMAIN error
    return RemoveBindingResult(...)

# routes/ — the ONLY layer that knows HTTP
try:
    result = remove_binding(cmd)
except BindingNotFound as e:
    raise HTTPException(status_code=404, detail=str(e))
```

So: `application/` raises domain errors / returns domain results; `routes/` maps them; `services/`
does infra side effects (and likewise should raise domain/infra errors, not `HTTPException`).

## 3. Target error/result style

- One small `errors.py` (or per-module domain errors) carrying the **message verbatim** (and any
  structured payload, e.g. `config_apply`'s `list[str]`). Route maps `status_code` + `detail`
  byte-identically — the status codes above are the contract, preserved exactly.
- Response **models** (the Pydantic `*Response` classes) may stay where they are; they are DTOs, not
  FastAPI control-flow. Only `HTTPException` (control flow) is the leak. (`depth_mux_proxy` is the
  exception — see DB.)

## 4. Per-module migration order (by blast radius, not size)

- **D3.1 — EASY, isolated, well-tested:** `encoder_admin` → `services_admin` → `config_apply`. Pure
  raise→domain-error swaps; one route caller each (`admin_dashboard` / `admin_config`); rich oracles.
- **D3.2 — service leaves:** `soak_files`, `proxy_base`. Self-contained service modules; `proxy_base`
  is a base class (de-leak ripples to its subclasses' routes — check `depth_camera_proxy`).
- **D3.3 — the entangled cluster:** `encoder_env` (svc, 400) → then `provision_stream` (drop its
  `except HTTPException` once `encoder_env` no longer raises it) **and** `mountpoint_admin` (the
  injected `create_mountpoint`). Do `encoder_env` + `mountpoint_admin` first, then `provision_stream`.
- **D3.4 — DECISION REQUIRED:** `depth_mux_proxy` (see DB). Not a mechanical swap.

## 5. Route mapping plan

Each route caller currently lets `HTTPException` propagate (FastAPI catches it). After de-leak the
route wraps the call in `try/except DomainError → HTTPException(status, detail)` — exactly the
`stream_bindings` pattern. Callers to touch: `routes/admin_dashboard.py` (encoder/services/
mountpoint/provision), `routes/admin_config.py` (config_apply), `routes/depth.py` (depth_mux_proxy),
plus any route that surfaces a `proxy_base` subclass. No path/method/auth/response-shape change.

## 6. Test oracle (must stay green, byte-identical status+detail)

`test_encoder_admin`, `test_services_admin` / `test_system_service`, `test_config_admin` /
`test_runtime_apply_ae1`, `test_mountpoint_admin`, `test_dashboard_misc` (provision), `test_system_routes`
(depth), plus service tests for soak/proxy_base. Each de-leak: characterize the HTTP status+detail at
the route (oracle), move to domain-error + route-map, add a use-case unit test asserting the domain
error directly. Full non-e2e suite must stay at only the known ColorView flake.

## 7. Red lines

No behavior change · no response-shape change · no **status-code or detail-string** change (the
table above is the contract) · no service rewrite · no config-model rewrite · no route-split campaign
· no unrelated cleanup · **do not touch `stream_binding_store`** (Phase 13 is done). One gated commit
per module-group; design-note + GO before code.

## 8. Acceptance

Every `raise HTTPException` in `app/application/**` removed (or, for `depth_mux_proxy`, resolved per
DB); the `app/services/**` leaks resolved or explicitly scoped-out; `application/` has no
`from fastapi import HTTPException`; routes map domain errors with identical status+detail; existing
oracles green + new use-case unit tests; full non-e2e suite only the ColorView flake.

## 9. Decisions (resolved 2026-06-21, user-gated)

- **DA — scope: app + service.** De-leak all 9 modules so `application/` AND `services/` are
  FastAPI-free (the 3 service modules are small). depth_mux_proxy excepted (DB).
- **DB — `depth_mux_proxy`: accepted exception.** It is an HTTP-boundary proxy adapter (returns
  `Response` objects, passes upstream status through) — documented NOT-debt, like the `/depth*` compat
  surface. NOT de-leaked.
- **DC — start with `encoder_admin`.** Then `services_admin`, `config_apply`, then the service leaves
  and the entangled cluster.

Note for D3.1: `encoder_action` is called by the encoder routes **and** by `provision_stream`, so its
new domain error (`EncoderExecFailed`) must be mapped at BOTH route call-sites (both in
`admin_dashboard`) to keep the 500 detail byte-identical.

**Progress (one gated commit per slice; each: domain errors → route map → re-point use-case tests →
route test; byte-identical status+detail; full suite only ColorView):**
- ✅ **D3.1** `encoder_admin` — `2605d4e`
- ✅ **D3.2A** `services_admin` — `00f6981`
- ✅ **D3.2B** `config_apply` (list[str] detail preserved) — `42294ae`
- ✅ **D3.3A** `soak_files` (svc, 400/404) — `49bd64b`
- ✅ **D3.3C** entangled cluster (`encoder_env` + `mountpoint_admin` + `provision_stream`) — `c2cc02c`
- ✅ **D3.4** closeout (this commit) — see below. **D3 CLOSED.**

## D3.4 closeout — D3 CLOSED (2026-06-21)

**Recon refinement.** The FastAPI "leak" beyond the 7 domain modules is the **proxy family — 6
modules, not the 3 service modules first reported**: `application/depth_mux_proxy.py` +
`services/{proxy_base, depth_camera_proxy, janus_proxy, realsense_mux_proxy, ws_proxy}.py`. Every one
is an HTTP/WS-proxy **adapter** — it takes a FastAPI `Request`, returns a FastAPI `Response`, and
passes upstream status through; the `HTTPException`s are its error-mapping, inseparable from its job.
Per DB (and the `proxy_base` decision, extended to the whole family) these are **accepted exceptions**
(NOT-debt, HTTP-boundary infrastructure), not de-leak targets — a "de-leak" would mean they stop being
proxies.

**All 7 DOMAIN modules are now FastAPI-free** (`encoder_admin`, `services_admin`, `config_apply`,
`soak_files`, `encoder_env`, `mountpoint_admin`, `provision_stream`): they raise domain errors; the
routes map them with byte-identical status+detail.

**Locked by a fitness guard:** `test_architecture_fitness.test_application_and_services_are_fastapi_free_except_proxy_adapters`
fails on any `HTTPException` in `app/application/**` or `app/services/**` outside the 6-module
`_HTTP_ADAPTER_ALLOWLIST`. Full non-e2e suite only the known ColorView flake; fitness green.

**D3 (and the whole D1→D2→D3 architecture campaign) is CLOSED.** The proxy adapters are the documented
HTTP boundary, like the `/depth*` compatibility routes.

## 9b. Original open decisions (for the record)

- **DA — scope: app-only, or app + service?** The truth-map's D3 named only the 6 app use-cases, but
  the leak reaches `services/{encoder_env,soak_files,proxy_base}`. `provision_stream` can't be fully
  de-leaked without `encoder_env`. **Recommend:** include the 3 service modules (they're small: 1+2+4
  raises) so D3 actually closes "`application/` + `services/` are FastAPI-free", rather than leaving a
  half-leak. Alternative: app-only now, service leaks as a tracked follow-up (D3b).
- **DB — `depth_mux_proxy`:** it *returns* FastAPI `Response`/`JSONResponse` and passes through upstream
  status codes — it is an HTTP-proxy **adapter**, not a domain use-case. Three options:
  1. **Accept as a documented exception** (it lives at the HTTP boundary by nature; mark it NOT-debt
     like the `/depth*` compat routes). Lowest effort, honest.
  2. **Relocate to `routes/`** (it's route-level — returns Response objects). Medium; no behavior change.
  3. **Full de-leak** (return raw bytes/dicts + domain errors; route rebuilds the Response with
     `X-Width/...` headers). Large, risky, and fights "no response-shape change".
  **Recommend option 1 or 2** (exception or relocate), NOT 3.
- **DC — start module:** recommend **`encoder_admin`** first (3 raises, one caller, strong oracle) as
  the pattern-proving slice, then `services_admin`, `config_apply`.
