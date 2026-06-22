# STREAM_BINDING_STORE_SPLIT — Phase 13 (D2) recon + design note

`services/stream_binding_store.py` is **892 lines, 33 public symbols + 16 private helpers**, with
**~10 responsibilities** in one module and **49 caller modules** repo-wide. This note is the recon
+ plan. **No code until approved.** Companions: [../ARCHITECTURE_CURRENT.md](../ARCHITECTURE_CURRENT.md)
(D2 = top debt) · [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) ·
[STREAM_BINDINGS_ROUTE_CLEANUP.md](STREAM_BINDINGS_ROUTE_CLEANUP.md) (D1, the precondition, now CLOSED).

This is **not** a route refactor — it is a cross-cutting storage/domain split. The discipline is the
same (characterize → move verbatim → re-point → one gated commit per step), but the blast radius is
the whole app, so the plan is **facade-first, leaves-first, no big-bang**.

## 1. Responsibility inventory (what lives in the file)

| # | Responsibility | Symbols (public unless _private) | Lines |
|---|---|---|---|
| R1 | **Domain models / enums / errors** | `StreamMode`, `StreamStatus`, `StreamTransport`, `StreamJanusConfig`, `StreamFdirConfig`, `NodeEntry`, `StreamBinding`, `BindingValidationError`, `StoreCorruptionError`, `LOCAL_NODE`, `LOCAL_NODE_ID`, `LOOPBACK` | 83–248, 359 |
| R2 | **Persistence: flock + atomic write + load** | `_flock_state`, `_load_state`, `_normalize_state` | 276–298, 386–421 |
| R3 | **Corruption quarantine (fail-closed)** | `StoreCorruptionError`, `_quarantine_corrupt_state`, `store_corruption_status` | 359–433 |
| R4 | **Per-node secret store (0600 token)** | `_secrets_path`, `_read_secrets`, `_set_node_secret`, `_remove_node_secret`, `_with_token`, `mint_agent_token`, `set_agent_token` | 300–356, 632–644 |
| R5 | **Node table CRUD** | `get_node`, `list_nodes`, `upsert_node`, `add_node_by_host`, `remove_node`, `set_reachability`, `set_serial`, `set_provision_state`, `touch_checked`, `set_maintenance`, `set_host_key`, `_mint_ordinal` | 453–644, 769–789 |
| R6 | **Local projection (read-only, from allocator)** | `_project_local` | 649–671 |
| R7 | **Binding read (merged local+remote)** | `list_bindings`, `get_binding` | 676–692 |
| R8 | **Binding write (remote only) + FDIR flag + id rekey** | `upsert_binding`, `remove_binding`, `set_status`, `set_fdir_enabled`, `_validate_remote`, `remote_binding_id`, `migrate_remote_binding_ids` | 697–858 |
| R9 | **Allocation policy (remote, above legacy pool)** | `allocate_mountpoint`, `allocate_port`, `_used_sets` + constants `REMOTE_MP_MIN/REMOTE_PORT_MIN/NODE_MP_WINDOW/NODE_PORT_WINDOW/MAX_REMOTE_NODES` | 436–448, 861–891 |
| R10 | **LAN validation (service-layer invariants)** | `_is_ipv4`, `_is_loopback` + `GATEWAY_LAN_IP`/`CAMERA_LAN_CIDR` checks embedded in `add_node_by_host` (R5) and `_validate_remote` (R8) | 261–273, 539–550, 713–719 |

## 2. Caller map (why a blind split is dangerous)

**49 caller modules**: routes (3) · use-cases (19, the `stream_bindings/*`) · services (10) · core (1) ·
tests (16). Highest-coupling symbols (callers): `StreamMode` (22) · `LOCAL_NODE_ID` (17) · `get_node`
(17) · `get_binding` (14) · `StreamBinding` (12) · `list_bindings` (11) · `add_node_by_host` (11) ·
`DEFAULT_STATE_PATH` (11). Heaviest single caller: `services/node_provisioner` (18 symbols).

Takeaway: **the domain models (R1) are imported almost everywhere.** Any split MUST keep
`from app.services import stream_binding_store as sbs; sbs.StreamMode/StreamBinding/get_node/…`
working unchanged — i.e. a **stable facade is mandatory and probably permanent**.

## 3. Hard constraints / gotchas the split MUST respect

1. **One state file, one lock.** `nodes` and `bindings` live in a single `stream_bindings.json` under a
   single `flock` (`_flock_state`). You cannot give "node_repository" and "binding_repository"
   independent storage — they share the file and lock.
2. **`remove_node` is a cross-entity transaction** (lines 769–789): under ONE lock it deletes the node
   row AND every binding it owns AND (outside the lock) the 0600 token. A naive node/binding split
   breaks this atomicity. → repos must be *function groups over a shared state dict*, not separate stores.
3. **Node CRUD inherently touches R4 + R9 + R10.** `upsert_node`/`add_node_by_host` mint a token
   (secrets), assign an allocation `ordinal`, and enforce LAN invariants; `get_node`/`list_nodes`
   overlay the token via `_with_token` on every read. So R5 depends on R4/R9/R10 — they are not cleanly
   independent layers.
4. **Binding write + allocation + projection are intertwined.** `upsert_binding` validates against
   `_used_sets` (R9, union of store + allocator); `list_bindings` merges `_project_local` (R6);
   `allocate_*` calls `get_node` (R5). Keep this call-graph co-located or the cross-module imports
   multiply.
5. **Monkeypatch-visibility hazard (the big one).** A facade that re-exports `from .nodes import
   get_node` exposes `sbs.get_node`, but an intra-package caller (`bindings.allocate_mountpoint` →
   `get_node`) binds the *submodule's* name, not the facade's. Tests that `monkeypatch.setattr(sbs,
   "get_node", …)` (the use-case unit tests do exactly this) would then NOT affect intra-package calls.
   **Rule for the split:** cross-responsibility calls inside the package go through a single canonical
   binding, and tests stub at the defining submodule; OR keep mutually-calling functions in the same
   submodule. This must be validated against the existing oracle at each step.
6. **`store_corruption_status` / `StoreCorruptionError` fail-closed semantics (R3, review H-02)** must
   be byte-identical — a silent empty-reset would let the reconciler tear down a live fleet. Covered by
   `test_store_corruption.py` (10 tests); do not perturb.
7. **Secret-store 0600 + atomic + token-never-in-topology-file (review H3)** must be preserved exactly
   (R4). `to_dict()` deliberately drops `agent_token`.

## 4. Proposed target shape (facade-first)

Convert the module into a **package** whose `__init__.py` is the facade; callers keep
`from app.services import stream_binding_store as sbs` unchanged.

```
app/services/stream_binding_store/
  __init__.py        # FACADE: re-exports the full current public API (stable surface)
  models.py          # R1 — enums, frozen dataclasses, errors, LOCAL_NODE(_ID), LOOPBACK, constants
  state_file.py      # R2+R3 — _flock_state, _load_state, _normalize_state, quarantine, store_corruption_status
  secrets.py         # R4 — 0600 token store (_read/_set/_remove/_with_token, mint/set_agent_token)
  validation.py      # R10 — _is_ipv4/_is_loopback + LAN invariants (GATEWAY_LAN_IP/CAMERA_LAN_CIDR)
  nodes.py           # R5 — node CRUD (uses state_file + secrets + validation + models)
  bindings.py        # R6+R7+R8+R9 — projection + read + remote write + allocation (intertwined; one module)
```

Rationale for grouping R6–R9 into one `bindings.py`: per §3.4 they share `_used_sets`, the allocator
dependency, and the node lookup — splitting them creates more cross-imports than it removes. Allocation
*may* graduate to its own `allocation.py` later if it proves separable, but not up front.

**The facade is the deliverable, not an interim hack.** With 49 callers and models used by 22, a curated
`__init__.py` public surface is the right long-term API. Re-pointing callers off the facade (13E) is
*optional polish*, not required to close D2.

## 5. Split order (leaves-first, one gated commit each)

Dependency direction: `models ← state_file ← secrets ← validation ← nodes ← bindings`. Extract leaves
first so nothing ever has to call *back* into the facade.

- **13A — `models.py`** (R1). Pure data, zero logic, nothing calls back. Facade re-exports. Lowest risk;
  proves the package+facade pattern. Oracle: `test_stream_binding_store.py` model round-trips +
  `test_node_add_by_host::test_node_entry_roundtrips_new_fields`.
- **13B — `state_file.py`** (R2+R3). Leaf persistence primitive (depends only on json/os/fcntl + models'
  nothing). Facade re-exports `store_corruption_status`, `StoreCorruptionError`. Oracle:
  `test_store_corruption.py` (all 10).
- **13C — `secrets.py`** (R4). Leaf (state_path + json only). Oracle: `test_node_add_by_host` token tests
  (`mints_unique_per_node_token`, `set_agent_token_persists…`).
- **13D — `validation.py`** (R10). Tiny leaf helpers. Oracle: `add_by_host_rejects_bad_addresses`,
  LAN-invariant tests.
- **13E — `nodes.py` + `bindings.py`** (R5–R9). The coupled core. Move together (they share the lock and
  the cross-entity `remove_node`). Most care here: §3.5 monkeypatch rule + §3.2 transaction. Oracle: the
  bulk of `test_stream_binding_store.py` (31) + `test_node_add_by_host` (11) + the use-case suite (77).
- **13F (optional) — caller migration + facade shrink.** Only if we decide to wean callers off the
  facade. Likely deferred indefinitely; the facade stays.

Each step: `stream_binding_store` keeps the SAME public API; full non-e2e suite must stay at only the
known ColorView flake; new module-level tests added where a seam is now directly unit-testable.

## 6. Characterization inventory (safety net) + gaps to fill first

Strong existing coverage — **52 store-focused tests**: `test_stream_binding_store.py` (31),
`test_store_corruption.py` (10), `test_node_add_by_host.py` (11) — plus heavy indirect coverage via
`test_node_provisioner` (11), `test_operator_console` (25), `test_reconcile_drift` (13),
`test_reconcile_run_once` (8), `test_remote_stream_monitor` (13), and the use-case suite (77).

Before 13E, **confirm** characterization exists for the high-risk verbatim moves (add as `13.0`
characterization commits if missing): the `remove_node` cross-entity cascade (node + bindings + secret
in one op), the `_used_sets` union (store + allocator) under concurrent upsert, `migrate_remote_binding_ids`
rekey-preserves-allocation, and the secret-store 0600 perm bit.

## 7. Definition of done for D2

D2 is CLOSED when: `stream_binding_store` is a package of responsibility modules behind a stable facade;
no single module carries >2 of the R1–R10 responsibilities; the public API (`sbs.*`) is byte-identical;
full non-e2e suite is green (only ColorView); and each responsibility has at least one directly-addressed
characterization test. Caller migration off the facade is **out of scope** for "closed".

## 8. Red lines

No big-bang rewrite (facade-first, one responsibility per gated commit). No behavior change to: the
fail-closed corruption path (R3), the 0600 secret semantics (R4), LAN invariants (R10), allocation
windows (R9), the `remove_node` transaction (§3.2), or any public signature. Do NOT touch
`mountpoint_allocator`, `firewall_sync`, `node_provisioner`, `binding_provision`, or the reconcile
engines — they are callers, not in scope. The facade must keep `from app.services import
stream_binding_store as sbs` working for all 49 callers at every step.

## 9. Open questions for approval (before any code)

- **DA — package vs sibling private module?** Recommend the package + `__init__.py` facade (co-locates
  the pieces under the store's name; zero caller churn). Alternative: keep `stream_binding_store.py` as a
  thin facade importing a new `app/services/_sbstore/` package.
- **DB — bindings granularity:** keep R6+R7+R8+R9 as one `bindings.py` (recommended, per §3.4), or split
  `allocation.py` out up front?
- **DC — start point:** 13A (`models.py`) first as the pattern-proving slice, or a `13.0` characterization
  pass first to backfill the §6 gaps before any move?

## Closeout (2026-06-21) — D2 CLOSED

Resolved (user-gated): **DA** package + `__init__` facade · **DB** one `bindings.py` · **DC** start at
13A. Executed leaves-first then the coupled core, one gated commit each, full suite green throughout:

| Phase | Module | Resp | Commit |
|---|---|---|---|
| 13A | `models.py` | R1 | `f9bf2b5` |
| 13B | `state_file.py` | R2+R3 | `ba9f303` |
| 13C | `secrets.py` | R4 | `536e2b3` |
| 13D | `validation.py` | R10 | `363b3f8` |
| 13E1 | `nodes.py` | R5 | `b28a65c` |
| 13E2 | `bindings.py` | R6–R9 | `4d88c0a` |

`__init__.py` is now a **pure re-export facade** (zero logic) exposing the full **52-symbol** public API;
class identity preserved; all 49 callers unchanged. The §3 hazards held: the one cross-module call
(`bindings.allocate_* → nodes.get_node`) is one-directional (no cycle); `remove_node`'s cross-entity
transaction stayed intact in `nodes.py`; the monkeypatch surface survived with **one** minimal re-point
(`test_stream_binding_store` LAN tests `sb.GATEWAY_LAN_IP` → `sb.nodes.GATEWAY_LAN_IP`, where
`add_node_by_host` now reads it). The `secrets.py` self-shadow was dodged with `from secrets import
token_urlsafe` (absolute import, verified). Truth-map reconciled; **D3** (application-FastAPI de-leak) is
now the top remaining debt.
