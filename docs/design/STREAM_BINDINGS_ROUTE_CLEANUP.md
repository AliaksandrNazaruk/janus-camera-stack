# STREAM_BINDINGS_ROUTE_CLEANUP — Phase 12.3 (finish route orchestration, D1)

Phase 12 closed the heavy topology (activate/delete). **11 of 25** endpoints are now thin; **14
still orchestrate inline**. Phase 12.3 extracts the remaining inline handlers in small, gated
groups so `routes/stream_bindings.py` reaches "parse + auth + call use-case + map" *before* the
store split (Phase 13). Same discipline: the existing route oracle stays green (characterization)
→ move verbatim → add use-case unit tests → one gated commit per group. Companions:
[STREAM_BINDINGS_HEAVY_TOPOLOGY_EXTRACTION.md](STREAM_BINDINGS_HEAVY_TOPOLOGY_EXTRACTION.md) ·
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) D1.

## Groups (safest first)

- **12.3A — read/list/view:** `get_nodes`, `get_stream_bindings`, `fleet_plan`, `reconcile_drift`.
- **12.3B — node check / maintenance / host-key.**
- **12.3C — create binding / fleet reconcile / firewall reconcile.**

## 12.3A — decisions (resolved 2026-06-20, user-gated)

- **Scope: extract ALL 4 reads uniformly** (layer consistency), even though `get_nodes` /
  `fleet_plan` are near-thin single-service reads. Use-cases:
  `application/stream_bindings/{list_nodes,list_bindings,fleet_plan,reconcile_drift}.py`.
- **DTO mapping stays in the route.** `_node_out` / `_binding_out` are HTTP-boundary concerns;
  the use-cases return **domain** data (NodeEntry map · `(binding, rtp_age)` pairs · plan dict ·
  drift report dict) and the route shapes the response.
- **RTP freshness is INJECTED.** `_rtp_age` (best-effort Janus probe) stays a route helper and is
  passed into `list_bindings` + `reconcile_drift` as `rtp_age_fn` — mirrors the existing
  `compute_drift(rtp_age_fn=…)`, keeps the use-cases free of the Janus client, and preserves the
  best-effort/swallow semantics exactly.
- **`plan_dict`** (was the route's `_fleet_plan_dict`) moves into `fleet_plan.py` and is reused by
  the still-inline `fleet_reconcile` (re-pointed now to avoid duplication; behavior identical).
  12.3C moves `fleet_reconcile` itself.
- **Error mapping (route maps domain errors).** `ManifestInvalid` → **422** (message verbatim);
  `JanusUnreachable` → **503** `janus_unreachable: <reason>` (reason = `str(e)[:120]`,
  byte-identical). `StoreCorruptionError` is **NOT** caught — it propagates from `list_bindings`
  through the use-case and the route to the app's 503 `topology_store_corrupt` handler **[R5]**.

## 12.3A — oracle (already route-level; must stay green)

- `get_nodes` — `test_stream_bindings_api::test_get_nodes_includes_implicit_local`
- `get_stream_bindings` — `test_stream_bindings_api::test_list_bindings_shows_created`;
  `test_operator_console::test_list_bindings_includes_rtp_age_when_requested`
- `fleet_plan` — `test_stream_bindings_api::test_fleet_plan_then_reconcile`, `::test_fleet_plan_bad_manifest_422`
- `reconcile_drift` — `test_reconcile_drift::test_route_corrupt_store_raises_for_503_R5`
  (`sb.reconcile_drift()` stays **arg-less** + must propagate `StoreCorruptionError`) +
  `::test_route_is_read_only_R4` (the route function source must stay free of
  `ensure_janus(` / `create_mountpoint(` / `destroy_mountpoint(` / `reconcile_gateway(` /
  `subprocess` — trivially true once the handler is thin).

New: use-case unit tests in `test_stream_bindings_usecases.py` pin each contract directly
(list_nodes; list_bindings with/without rtp_age via an injected fn; fleet_plan happy + ManifestInvalid;
reconcile_drift happy + JanusUnreachable + maintenance filter + StoreCorruption propagation).

## 12.3B — node check / maintenance / host-key (decisions, 2026-06-21)

Handlers: `check_node`, `set_node_maintenance`, `get_node_host_key`, `confirm_node_host_key` →
`application/stream_bindings/{check_node,set_maintenance,get_host_key,confirm_host_key}.py`.

- **`_live_node` is REMOVED from the route.** It is used only by the two host-key handlers and only
  raises `HTTPException` (404/400) — exactly the orchestration leak we're closing. Its validation
  folds into both host-key use-cases as domain errors (`NodeNotFound` / `LocalNodeNoHostKey`).
  `_node_for_provision` + `_transport_for` **STAY** (shared with provision/activate — out of scope).
- **Injection preserves the oracle untouched.** The host-key oracle patches
  `sb_routes.capture_host_key` / `sb_routes.host_key_fingerprint` (the *route module's* names). So
  those two callables are **injected** into `get_host_key` + `confirm_host_key` (the route passes its
  own module-level names) — same pattern as 12.3A's `rtp_age_fn`. `check_node` needs no injection
  (its oracle patches `httpx.get`, transparent to `node_client.probe_agent`); `set_maintenance` needs
  only the injected state path.
- **DTO mapping stays in the route.** `set_maintenance` returns the domain `NodeEntry` → route maps
  `_node_out`. `check_node` + host-key use-cases build + return plain dicts (already domain-shaped).
- **Domain errors (route maps).** `NodeNotFound` → 404 (reused); `MaintenanceLocalRejected` → 400;
  `LocalNodeNoHostKey` → 400; `HostKeyUnreachable` → 503; `HostKeyFingerprintMismatch` → 409;
  `HostKeyPinReplaceRejected` → 409. The audit calls (including the two rejection-path audits in
  confirm) move **verbatim** into the use-cases.

Oracle (route-level, must stay green): `test_stream_bindings_api::{test_node_check_unreachable_bootstrap,
test_node_check_local_reachable, test_host_key_confirm_pins_on_match_rejects_on_mismatch,
test_host_key_confirm_refuses_silent_repin}`; `test_operator_console::{test_maintenance_endpoint_toggles,
test_maintenance_rejects_local}`. New use-case unit tests pin each contract.

## 12.3C — create binding / fleet reconcile / firewall reconcile (decisions, 2026-06-21)

Handlers: `create_stream_binding`, `fleet_reconcile`, `firewall_reconcile` →
`application/stream_bindings/{create_binding,fleet_reconcile,firewall_reconcile}.py`.

- **No injection needed** (unlike 12.3B). All three oracles patch `app.services.*` shared modules
  (`fleet.load_manifest`, `firewall_sync.reconcile`) or just use the redirected store — so the
  use-cases call the service modules directly and the patches still reach them.
- **DTO stays in the route.** `create_binding` returns the domain `StreamBinding` → route maps
  `_binding_out`. `fleet_reconcile` reuses `plan_dict` (relocated in 12.3A); `firewall_reconcile`
  returns a plain summary dict.
- **Domain errors (route maps).** create: `LocalBindingNotCreatable` → 400,
  `BindingNodeNotFound` → 404 (distinct message "… — register it first", so *not* the generic
  `NodeNotFound`), `AllocationConflict` → 409 (wraps `mountpoint_allocator.AllocationError` message),
  `BindingInvalid` → 400 (wraps `sbs.BindingValidationError` message). fleet: `ManifestInvalid` → 422
  (reused from 12.3A). firewall: none (a `firewall_sync` failure 500s, as before). Audit calls move
  verbatim.
- **`reconcile_janus_run_once` stays inline** — it calls `binding_provision.run_janus_reconcile_once`
  (reconcile_janus red-line) and wasn't in this group; deferred to the 12.3D assessment together with
  `register` / `add_node`.

Oracle (route-level, must stay green): `test_stream_bindings_api::{test_create_binding_autoallocates,
test_create_binding_unknown_node_404, test_create_binding_local_node_400,
test_create_binding_loopback_iface_rejected, test_list_bindings_shows_created,
test_fleet_plan_then_reconcile, test_firewall_reconcile_endpoint_dryrun}`. New use-case unit tests
pin each contract.

## 12.3D — node register / add-by-host (decisions, 2026-06-21)

Final stragglers. User-gated: **extract `register_node` + `add_node`** (uniformity with the
node-CRUD set); **leave `reconcile_janus_run_once` inline** — it's already thin (one
`binding_provision.run_janus_reconcile_once` call + 503-map + audit) and calling that engine is
reconcile_janus red-line territory.

- Handlers → `application/stream_bindings/{register_node,add_node}.py`. Both return the domain
  `NodeEntry` → route maps `_node_out`.
- **`gateway_lan_ip` is INJECTED** into `add_node` (route passes its `GATEWAY_LAN_IP` constant) so
  the existing `sb_routes.GATEWAY_LAN_IP` patch surface keeps working — same injection pattern as
  12.3B's host-key fns.
- Domain errors: `NodeRegistrationInvalid` → 400 (wraps `sbs.BindingValidationError`, shared by
  both); `AddNodeIsLocalGateway` → 400 (rejects the gateway's own addresses, carries host +
  LOCAL_NODE_ID for the verbatim hint). Audit calls move verbatim.

Oracle (route-level): `test_stream_bindings_api::{test_register_node_then_list,
test_add_node_by_ip_mints_node_id}` (redirected store). New use-case unit tests pin each contract
(incl. the parametrized local-gateway reject + injected-ip proof).

**Phase 12 / route-cleanup CLOSED.** The route's heavy orchestration is fully in the application
layer (23 use-cases); the only remaining inline handler is `reconcile_janus_run_once` (deliberate,
documented above). Next: Phase 13 — `stream_binding_store` split (D2).

## Acceptance (per group)

Route paths/methods/auth/rate-limit/response shape unchanged; the read-only guarantee for
`reconcile_drift` preserved; best-effort `_rtp_age` swallow preserved; `application/` stays
FastAPI-free (domain results/errors; the route maps); existing oracle green + new use-case unit
tests; full suite only the known ColorView flake.

## Red lines

No change to `fleet` / `reconcile_drift` / `janus_admin` / `sbs` behavior (call them, don't change
them); keep the HTTP-boundary helpers (`_node_out`, `_binding_out`, `_rtp_age`, `_require_lan_ipv4`,
`_transport_for`, `_node_for_provision`, `_spawn_node_op`) in the route; state paths injected via
commands; **no `stream_binding_store` split** (that's Phase 13, after 12.3 thins the callers).
