# STREAM_BINDINGS_HEAVY_TOPOLOGY_EXTRACTION — Phase 12 (D1, heavy tier)

The heavy `stream_bindings.py` handlers the small-vertical phase (10.x) deliberately left:
`activate_node_streams` and `delete_node`. They are topology ops (SSH, Janus teardown, firewall
reconcile, cascading store mutation, partial failures) — extracted only now that Phase 11 gives a
durable operation runner. Same discipline: recon → design note → characterization → move verbatim
→ route maps result/errors → one gated commit per vertical. Companion:
[STREAM_BINDINGS_EXTRACTION.md](STREAM_BINDINGS_EXTRACTION.md) · [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) D1.

**Order (per review):** **12.1 activate** first (already long-op-shaped; the Phase-11 runner was
built for it), then **12.2 delete** (more dangerous — cascade + Janus teardown + store cleanup).
**Recon + design only here. No code until approved.**

## Recon

### `activate_node_streams` (routes:543)
| | |
|---|---|
| validate | sensors ∈ {color,depth,ir1,ir2} else 400 |
| **local** (cam10) | `_activate_local_streams(sensors)` — **synchronous**; `sensor_lifecycle.initialize` per sensor; per-sensor error channel; returns `{node_id, sensors, started, poll:None, results}` |
| **remote** | `_require_lan_ipv4(gateway_host)`→400 · `_node_for_provision`→404/400/503 · `_transport_for`→412 (host-key) · `make_gateway_binder` · then **async** `_spawn_node_op("activate", _activate_then_firewall)` |
| `_activate_then_firewall` | `node_provisioner.activate_streams(…on_bind=binder…)` then best-effort `firewall_sync.reconcile(apply=True)` (swallowed) |
| returns (remote) | `{node_id, sensors, started:True, poll:"GET …/stream-bindings"}` |
| rollback | none — partial activation is surfaced via binding status + StreamResult; not compensated |

### `delete_node` (routes:362) — **synchronous cascade**
| step | semantics |
|---|---|
| validate | 404 unknown · 400 local cam10 |
| (opt) `deprovision` | per-binding `client.stop_stream` — **best-effort**, swallowed |
| Janus teardown | per-binding `janus_admin.destroy_mountpoint` — best-effort, swallowed (the `@_with_handle` one; works) |
| **store** | `sbs.remove_node` — the ONLY must-succeed mutation (no try/except); removes node row + all its bindings |
| firewall | `firewall_sync.reconcile(apply=True)` — best-effort, swallowed → `firewall_reconciled` flag |
| returns | `{node_id, removed, removed_bindings, destroyed_mountpoints, firewall_reconciled, deprovisioned}` (synchronous, full result) |

**Shared route helpers** (used by provision too, **NOT in scope to move**): `_node_for_provision`,
`_transport_for` (host-key 412 / TOFU), `_require_lan_ipv4`, `_live_node`.

## Decisions (resolved 2026-06-20, user-gated)

- **DA — orchestration bodies only.** Move `_activate_local_streams` →
  `application/stream_bindings/activate_local.py` and the `_activate_then_firewall` body →
  `application/stream_bindings/activate_remote.py` (fired through `runner.run`). The route keeps
  sensor validation (400) + the **shared** transport/host-key(412)/LAN(400) helpers (reused by
  provision — out of scope to move).
- **DB — `delete_node` stays SYNCHRONOUS.** Move the cascade to
  `application/stream_bindings/delete_node.py` returning a `DeleteNodeResult`; the route maps
  404/400 and builds the same synchronous response. No async / contract change.
- **DC — relocate firewall + Janus calls VERBATIM.** `firewall_sync.reconcile` /
  `janus_admin.destroy_mountpoint` move with the orchestration; best-effort/swallow preserved
  exactly. Those modules are untouched.

## Plan (pending decisions)

**12.1 activate** — ✅ **DONE**. `application/stream_bindings/`: `activate_local(cmd) -> dict` (verbatim
`_activate_local_streams`) + `activate_remote(...)` (verbatim `_activate_then_firewall` body). Route:
validate → local: call `activate_local`; remote: build transport (shared helpers) →
`runner.run(node_id, "activate", activate_remote, …)` → `{started, poll}`. Domain errors for the
remote `_node_for_provision`/`_transport_for` stay as route HTTP raises (shared). Oracle:
`test_stream_bindings_api::test_activate_streams_kicks_off`, `test_operator_console` local-activate.

**12.2 delete** — ✅ **DONE** (Phase 12 CLOSED). `application/stream_bindings/delete_node.py`: `delete_node(cmd) -> DeleteNodeResult`
(verbatim cascade: deprovision stop → destroy_mountpoint → `remove_node` → firewall reconcile,
best-effort preserved). Route: 404/400 map + build `{…}` response. Oracle: `test_operator_console`
delete (`removed_bindings`), `test_stream_bindings_api`.

## Acceptance (per vertical)

Route paths/methods/auth/rate-limit/response shape unchanged; local vs remote branch preserved;
best-effort/swallow semantics + partial-result fields (`destroyed_mountpoints`, `firewall_reconciled`,
per-sensor `results`) byte-identical; `runner.run` used for the remote async activate (409 + journal
intact); audit events unchanged; `application/` stays FastAPI-free (domain results/errors; the route
maps); existing oracle green + new use-case unit tests; full suite only the ColorView flake.

## Red lines

No change to `node_provisioner` / `firewall_sync` / `reconcile_janus` / `janus_admin` behavior
(call them, don't change them); no `delete_node` async redesign (DB); no moving the shared
transport/host-key helpers (DA); no `stream_binding_store` split (that's Phase 13, after these land
and the operation boundaries stabilize).

## Closeout (2026-06-20) — Phase 12 CLOSED, what's next

Both verticals landed (`a4c110b` activate, `9cbb8c1` delete). `stream_bindings.py` **776 → 748L**;
`application/stream_bindings/` is now **10 use-case files**; full suite only the known ColorView
flake. With this, **11 of 25 route endpoints are thin** (call a use-case); **14 still orchestrate
inline** (node CRUD, fleet, reconcile, firewall, binding list/create).

Sequencing recap (decided after 12.2): **do NOT jump to Phase 13** (store split). The store is the
highest-coupling module — the 14 inline handlers, all 10 use-cases, and `node_provisioner` /
`binding_provision` / `firewall_sync` / FDIR monitors all reach into it. Finish the route first:

- **Phase 12.3** — extract the 14 inline handlers in small groups: **12.3A** read/list/view (list
  nodes · list stream-bindings · fleet plan · reconcile drift) → **12.3B** node check / maintenance
  / host-key → **12.3C** create binding / fleet reconcile / firewall reconcile. Keep the HTTP-boundary
  helpers (`_node_out`, `_binding_out`, `_require_lan_ipv4`, `_transport_for`, `_node_for_provision`,
  `_spawn_node_op`) in the route.
- **Phase 13** — `stream_binding_store.py` split (D2), only once 12.3 has thinned the callers.

See [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) (D1 partial, D2 deferred)
and [../ARCHITECTURE_CURRENT.md](../ARCHITECTURE_CURRENT.md) for the reconciled truth-map.
