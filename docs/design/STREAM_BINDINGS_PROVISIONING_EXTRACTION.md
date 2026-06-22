# STREAM_BINDINGS_PROVISIONING_EXTRACTION — Phase 3 recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md). Extracts the residual
provisioning / SSH glue still inline in `routes/stream_bindings.py` (752L) — the audit's **A-02** debt
and the "residual provisioning glue" flagged as the next thinning candidate after D1. Behavior-preserving.
No code until GO.

## Recon — what's still inline (verified 2026-06-21)
D1 already extracted **24 use-cases** into `application/stream_bindings/`. What REMAINS inline:
- `_node_for_provision(node_id)` (331) — resolve node + reject the local node + check `NODE_BUNDLE_TAR`
  exists (404 / 400 / 503).
- `_transport_for(node, sudo_password, allow_tofu)` (342) — build `SSHTransport` with a PINNED host key;
  refuse **412** if `node.host_key` is unset unless `allow_tofu` (TOFU: `capture_host_key` + `set_host_key`
  + audit, then re-get the node). Host-key policy + transport construction.
- `provision_node` (367) / `rotate_node_token` (380) — resolve + `_transport_for` + spawn the durable op
  (`_spawn_node_op` → node_operation_runner) + audit + shape the `{operation_id, poll, operation}` response (H1).
- `activate_node_streams` (483) — already calls the `activate_remote` use-case; just shares
  `_node_for_provision` + `_transport_for`.
- `_rtp_age(mp_id)` (563) — RTP freshness probe (already injected as `rtp_age_fn` into reconcile_drift).
- `reconcile_janus_run_once` (599) — deliberate one-call delegation to the reconcile engine (a red-line; leave).

### The DI nuance (must preserve test oracles)
Tests patch route-module names: `sb_routes.capture_host_key`, `sb_routes.host_key_fingerprint`,
`sb_routes.node_client` (patch anchor), `sb_routes.sbs`, `sb_routes.NODE_BUNDLE_TAR`. So the extracted
use-case/adapter must take these as **injected** params (the route forwards its own patchable references) —
the same dependency-injection-to-preserve-oracles pattern D1 used (`rtp_age_fn`, `capture_host_key`/`fingerprint_fn`).

## Plan — sub-commits (tests-first, suite green between)
1. **char** — confirm/extend test_stream_bindings_api coverage for the provision/rotate/activate host-key
   paths: 412 not-confirmed, allow_tofu TOFU pin+audit, 404/400/503, 409 conflict, the op_id response shape.
2. **transport adapter** — `services/node_transport.py`: `build_transport(node, sudo_password, *, allow_tofu,
   capture_host_key, fingerprint_fn, store, audit_fn)` → `SSHTransport`, raising `HostKeyNotConfirmed` (domain;
   route maps 412). TOFU side effects (set_host_key + audit) injected. The route's `_transport_for` becomes a
   thin forwarder of its patchable names (or is removed with injections at the call sites — see D4).
3. **provision/rotate use-cases** — `application/stream_bindings/provision_node.py` + `rotate_node_token.py`:
   resolve + bundle-check + build transport + spawn the durable op; return op_id. Raise domain errors
   (NodeNotFound / LocalNodeRefused / BundleMissing / HostKeyNotConfirmed / OperationConflict / JournalCorrupt);
   the route maps them + audits + shapes the `{operation_id, poll, operation}` response (H1 unchanged).
4. **activate** — re-point activate_node_streams to the shared transport adapter (no behavior change).
5. **(optional) _rtp_age** → a small `services/rtp_freshness.py` if it is not already fully injected.

## Open decisions to gate (GO before any code)
- **D1 — transport factory home:** `services/node_transport.py` (infra adapter — it constructs SSHTransport)
  vs an application helper? (lean: services — it's infra.)
- **D2 — does the use-case SPAWN the durable op, or return prepared args for the route to spawn?** (lean:
  the use-case spawns via the injected runner; the route maps OperationConflict / JournalCorrupt so the H1/H3
  response + error mapping stay at the boundary.)
- **D3 — TOFU side effects (set_host_key + audit):** inside the transport adapter (injected store/audit) vs
  the route? (lean: the adapter, injected — keeps the host-key policy with the transport build.)
- **D4 — keep `_transport_for` / `_node_for_provision` as thin forwarders (patch anchors)** vs remove them and
  inline the injections at the call sites?

## Red lines
Behavior-preserving: same URLs, status codes (404 / 400 / 503 / 412 / 409), the TOFU pin+audit, the
`{operation_id, poll, operation}` response shape (H1), and the durable-op semantics (H1/H2/H3). Keep
`reconcile_janus_run_once` inline (red-line). Tests-first per sub-commit; never edit a characterization
assertion to make the refactor pass.

## Status — DONE (2026-06-21)
All sub-commits landed behavior-preserving; full non-e2e suite green at each step.
- **3-1** `a7bc0b2` — characterization tests filled the gaps the recon found: rotate-token
  (success + unknown-404) and the TOFU first-contact pin side effect. Provision / host-key-confirm /
  activate were already well-characterized (H1/H3 work).
- **3-2** `f896dd7` — `services/node_transport.build_transport(...)` adapter: host-key-confirmation
  policy + `SSHTransport` construction + the audited TOFU pin, raising domain `HostKeyNotConfirmed`
  (route maps 412). `_transport_for` is now a thin forwarder of the route's monkeypatchable
  collaborators (D1/D3/D4).
- **3-3** `f81f75a` — `application/stream_bindings/{provision_node,rotate_node_token}.py` use-cases:
  resolve → build transport → spawn the durable op → audit → return `NodeOpStarted`. Route handlers
  build the command, inject `_transport_for`/`_spawn_node_op` (which own the 412 / 409 / 503-journal
  mapping), map the resolve domain errors → 404/400/503, and shape the H1 response (D2/D3/D4).
- **3-4** `0b6f3a5` — `resolve_provision_target` use-case shares the provisionability policy across
  provision_node_uc and the activate path; `_node_for_provision` is now a thin forwarder over it
  (activate + the local_activate `_boom` anchor preserved). Removed the route's now-unused `import os`.
- **3-5** — **no-op**: `_rtp_age` is already DI'd as `rtp_age_fn` into the list/plan/reconcile
  use-cases; its route-level definition IS the injected adapter (same pattern as
  `_transport_for`/`_spawn_node_op`). Moving the definition to `services/` would be cosmetic only.

**Proof of the A-02 win:** the route now has **0** `SSHTransport(` constructions and **0**
`sbs.set_host_key` (host-key mutation) calls — both moved into `services/node_transport`. The remaining
provisioning surface in the route is boundary-only: thin forwarders (`_transport_for`,
`_node_for_provision`), the durable-runner wrapper (`_spawn_node_op`), the handlers (parse → use-case →
map → shape), and the deliberately-inline `reconcile_janus_run_once` (red-line).
