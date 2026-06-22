# STREAM_BINDINGS_ROUTE_SPLIT — Cycle 5 recon + plan (GATED, no code yet)

Closes the audit's "fat route" finding: `app/routes/stream_bindings.py` is 765 lines / 27 endpoints /
13 DTOs in one module. This is a STRUCTURAL cut, not a safety fix: physically split the fat router into a
package WITHOUT changing the API (same URLs, response shapes, auth, audit, operation-journal behavior).
No business logic moves — the routes already delegate to the `app.application.stream_bindings` use-case
layer; this only relocates handlers + DTOs into cohesive submodules. No code until GO.

## Recon — the module today (verified 2026-06-21)

**765 lines, 27 endpoints, prefix `/api/v1/admin`, `dependencies=[Depends(require_admin)]`, per-route
`_RL = Depends(require_admin_rate_limit)`.** Already well-layered: every handler is a thin adapter that
builds an `app.application.stream_bindings` Command and maps domain errors → HTTP. The "fat" is breadth
(many endpoints), not depth.

### Endpoint inventory — natural cohesion groups
- **nodes** (11): `GET /nodes`, `POST /nodes/register`, `POST /nodes`, `POST /nodes/check`,
  `POST /nodes/{id}/provision`, `POST /nodes/{id}/rotate-token`, `POST /nodes/{id}/maintenance`,
  `DELETE /nodes/{id}`, `GET /nodes/{id}/host-key`, `POST /nodes/{id}/host-key/confirm`,
  `POST /nodes/{id}/streams`
- **bindings** (9): `GET /stream-bindings`, `POST /stream-bindings`,
  `POST /stream-bindings/{id}/{ensure-janus,remove,restart,stop,fdir}`,
  `GET|POST /stream-bindings/{id}/tuning`
- **operations** (2): `GET /operations`, `GET /operations/{id}`
- **fleet + reconcile + firewall** (5): `GET /fleet/plan`, `POST /fleet/reconcile`,
  `POST /firewall/reconcile`, `GET /reconcile/drift`, `POST /reconcile/janus/run-once`

### DTOs (13 Pydantic models)
`NodeOut`, `NodeRegisterRequest`, `NodeAddByHostRequest`, `ProvisionRequest`, `HostKeyConfirmRequest`,
`StreamsRequest`, `NodeCheckRequest`, `MaintenanceRequest`, `FdirToggleRequest`, `BindingOut`,
`BindingCreateRequest`, `EnsureJanusResponse`, `TuningRequest`.

### Helpers (route-local)
`_require_lan_ipv4`, `_operations_path`, `_spawn_node_op`, `_node_out`, `_binding_out`,
`_node_for_provision`, `_transport_for`, `_rtp_age`, `_get_binding_or_404`.

### Shared module-level state — THE split constraint
`BIND_STATE_PATH = sbs.DEFAULT_STATE_PATH` and `ALLOC_STATE_PATH = mountpoint_allocator.DEFAULT_STATE_PATH`
are read as **module globals at ~30 handler call-sites** and passed into the use-case Commands
(`bind_state_path=BIND_STATE_PATH, alloc_state_path=ALLOC_STATE_PATH`). Plus the import "patch anchors"
`node_provisioner`, `node_client` (explicitly annotated `# patch anchor`), `janus_admin`, `sbs`,
`capture_host_key`, and the constants `NODE_BUNDLE_TAR`, `GATEWAY_LAN_IP`, `NODE_SSH_USER/KEY`.

### Test coupling (the behavior-preserving anchor — verified)
No test imports a SYMBOL from the module; no test patches `routes.stream_bindings.<handler>`. Coupling is:
- **`sb_routes.<attr>` patches** (3 files: `test_stream_bindings_api`, `test_operator_console`,
  `test_stream_bindings_local_activate`) — the central `_isolate_store` fixture does
  `monkeypatch.setattr(sb_routes, "BIND_STATE_PATH", tmp)` + `ALLOC_STATE_PATH`; others patch
  `sb_routes.{NODE_BUNDLE_TAR, capture_host_key, node_provisioner, node_client, janus_admin, sbs}`.
- **direct handler/helper calls** (3 files: `test_reconcile_drift`, `test_reconcile_run_once`,
  `test_operation_journal`) — `sb.reconcile_drift()`, `sb.reconcile_janus_run_once()`,
  `sb._spawn_node_op(...)`, and one `inspect.getsource(sb.reconcile_drift)`.

(The large `sb.upsert_node` / `sb.allocate_mountpoint` / `sb.json` grep counts were a false positive —
`sb` aliases the STORE in other files, and `sb.json` is a tmp filename. The true route coupling is small.)

So the split must keep these names resolvable as `app.routes.stream_bindings.<name>` AND keep
`monkeypatch.setattr(sb_routes, "BIND_STATE_PATH", X)` actually redirecting the store the handlers use.

## The split shapes (D1 — gate this FIRST)

Target package `app/routes/stream_bindings/` (a package replacing the single module). Submodules each own
an `APIRouter` (same prefix/deps) for their group; `__init__.py` assembles them into the public `router`.
DTOs → `contracts.py`. Helpers + shared state → `_shared.py`. Groups: `nodes.py`, `bindings.py`,
`operations.py`, `fleet.py` (fleet + reconcile + firewall). The difference is ONLY how the
`BIND_STATE_PATH`/`ALLOC_STATE_PATH` patch anchor is preserved:

- **(A) Re-point tests to the shared module ("patch at the source").** Anchors live in `_shared.py`;
  handlers read `_shared.BIND_STATE_PATH` at call time. Tests change `sb_routes.BIND_STATE_PATH` →
  `sb_routes._shared.BIND_STATE_PATH` (or import `_shared`). Explicit + consistent with Cycles 2–4, but
  the largest test churn so far (~6 files; the central `_isolate_store` fixture + ~25 setattr sites).
- **(B) Facade `__init__` as the shared namespace (zero test churn).** `__init__.py` holds the anchors +
  the `router`; submodule handlers read them THROUGH the package object at call time
  (`from app.routes import stream_bindings as _pkg; ... _pkg.BIND_STATE_PATH`). `monkeypatch.setattr(
  sb_routes, "BIND_STATE_PATH", X)` keeps working unchanged → **no test edits**. Cost: the
  package-as-shared-namespace pattern (submodule imports the partially-initialized package — safe because
  the access is call-time, not import-time; needs a one-line guard test that it imports cleanly).

**Lean: (B)** — the whole cycle is "no API/behavior change"; zero test churn is the strongest proof of
that, and the central isolation fixture is exactly the thing we don't want to perturb. (A) is cleaner
namespacing but trades the behavior-preservation proof for ~25 mechanical edits that each risk a typo.

## Plan — sub-commits (tests-first, suite green between) — assuming (B)
1. **route-inventory lock (char)** — a NEW test snapshotting the exact `{(method, path)}` set the
   stream_bindings router contributes under `/api/v1/admin` (27 routes) + that each carries
   `require_admin` (and the mutating ones `_RL`). This is the behavior-preserving oracle; it must stay
   green verbatim across the split. (Green now; stays green after.)
2. **extract `contracts.py` + `_shared.py`** — move the 13 DTOs to `contracts.py`; the helpers + shared
   anchors (`BIND_STATE_PATH`, `ALLOC_STATE_PATH`, imports, constants, `router` factory) to the package
   `__init__`/`_shared`. No handler moves yet; `stream_bindings.py` becomes the package `__init__` that
   still defines the handlers + re-exports. Suite green.
3. **move handlers into `nodes.py` / `bindings.py` / `operations.py` / `fleet.py`** — each gets its own
   sub-`APIRouter`; `__init__` includes them. Handlers read anchors via the package object (B). Suite
   green (incl. the step-1 inventory lock + the 6 coupled test files UNCHANGED).
4. **guard** — fitness guard: (a) the stream_bindings package is mounted and contributes the full route
   set (route-count floor), and (b) no submodule exceeds a line ceiling (the "stays un-fat" lock), or a
   lighter "package imports cleanly + router assembled" check. (Decide shape at D3.)

## Open decisions to gate (GO before any code)
- **D1 — split shape (A) re-point vs (B) facade-namespace.** Lean **(B)** (zero test churn = behavior
  proof). Yours — it trades a little import subtlety for not touching the isolation fixture.
- **D2 — group boundaries.** Lean: `nodes` / `bindings` / `operations` / `fleet`(=fleet+reconcile+firewall)
  + `contracts` + `_shared`. Alt: split `reconcile` out of `fleet` into its own module (5 → 6 files).
- **D3 — the Cycle-5 guard.** (a) route-inventory floor + per-submodule line ceiling, vs (b) just "package
  mounted + full route set present". Lean: (a) — it locks both completeness AND that the fat doesn't
  silently regrow.
- **D4 — keep `stream_bindings.py` as a shim?** No — convert to a package directory of the same import
  name (`app/routes/stream_bindings/`); `from app.routes import stream_bindings` resolves to the package.

## Red lines
No API change: identical URLs, methods, status codes, response models, `require_admin` + `_RL`
dependencies, audit events, and operation-journal behavior. No business logic moves — handlers stay thin
adapters over the existing `app.application.stream_bindings` use-cases; the use-case layer is untouched.
Preserve the `BIND_STATE_PATH`/`ALLOC_STATE_PATH` isolation anchor (under B, byte-for-byte unchanged
tests). Tests-first; the route-inventory snapshot is the oracle; never weaken it. Full non-e2e suite green
per sub-commit.

Expected: the fat router becomes a cohesive package (~5–6 focused files), each group independently
readable, with a guard that locks both route completeness and the no-refat invariant. Pure structure —
0 behavior delta. ~7.8–8.1 → ~7.9–8.2 on the structure axis (the safety axes are already closed).

## Status — DONE (2026-06-21)
Decisions as gated: **D1=(B)** facade-namespace (zero test churn); **D2** groups = nodes/bindings/
operations/fleet(+reconcile+firewall) + contracts + __init__ shared core; **D3=(a)** route-floor +
per-submodule line-ceiling guard; **D4** package (not a shim).
- **5.1** `18322b8` — `tests/test_stream_bindings_route_inventory.py`: froze the exact 27-route
  (method,path) surface — the behavior-preserving oracle.
- **5.2** `fe552ef` — `app/routes/stream_bindings.py` → `app/routes/stream_bindings/` package:
  `contracts.py` (13 DTOs); `__init__.py` (shared anchors + helpers + assembled `router` + re-exports);
  `nodes.py` (11) / `bindings.py` (9) / `operations.py` (2) / `fleet.py` (5), each on its own
  sub-APIRouter (same prefix + require_admin + _RL). Submodule handlers read the patchable anchors back
  through the package object (`_core.BIND_STATE_PATH`), so the API tests' `_isolate_store` fixture and
  every `sb_routes.*` patch work UNCHANGED — **zero test churn**, all 8 coupled test files pass verbatim.
  Handlers stay thin adapters over the existing use-cases (no logic moved). Lint parity exact
  (B904 52==52). Guard #11 refined to permit a route subpackage importing its OWN package (cohesion).
- **5.3** (this) — fitness guard **#22** `test_stream_bindings_is_a_cohesive_package`: it's a package
  (not the old module), still contributes ≥27 routes (floor; the exact set is the 5.1 inventory lock),
  and no submodule exceeds the 340-line ceiling (old monolith was 765; current max nodes.py ~252). **22
  fitness guards.**

**Result:** the 765-line fat router is now a cohesive package of focused files (≤252 lines each), with
identical URLs/methods/auth/audit/journal behavior, zero test churn, and a guard locking both route
completeness and the no-refat invariant. Pure structure — 0 behavior delta.
