# ADMIN_OPERATION_MODEL — Cycle 8A recon (operation-model + contract unification, GATED)

Cycle 7 made the NAT/TURN update an explicit staged operation. Cycle 8 asks: can the admin/runtime
operations be UNIFIED under an `AdminOperationRunner` without breaking semantics? The recon's answer up
front: **a single shared EXECUTOR is the wrong first step — the operations differ on two fundamental
axes. The real, low-risk win is a shared operation-result VOCABULARY/CONTRACT that they all map to**,
unifying the operator's read model. No code until GO.

## Inventory — the 4 admin/runtime operation mechanisms (verified 2026-06-21)

| mechanism | sync/async | id | history | status vocabulary | result shape | surfaced at |
|---|---|---|---|---|---|---|
| **Node ops** (`node_operation_runner` + `operation_journal`) | **async** (daemon thread) | `operation_id` (uuid) | durable journal (≤500, prune finished) | `running → succeeded / failed / interrupted` | journal record `{operation_id, op_type, node_id, status, started_at, finished_at, last_error}` | `GET /api/v1/admin/operations[/{id}]` |
| **Runtime-config apply** (`runtime_config_apply` + `runtime_revision_store`) | **sync** (under lock) | `revision_id` | revision store | `Outcome` enum (10: applied / rolled_back / rollback_failed / drift / conflict / lock_held / write_failed / not_found / confirm_mismatch / rejected) + store `validated → applying → applied / rolled_back / rollback_failed` | `ApplyResult{outcome, revision_id, changed, verified, detail, applied[]}` | `POST …/runtime-config/apply` |
| **NAT update** (Cycle 7, `update_nat_config` + status sidecar) | **sync** (stages) | — (none) | current-state only (sidecar) | sidecar `pending → applied / failed`; result `failure_stage` + applied-flags | `NatUpdateResult{ok, failure_stage, desired_persisted, local_applied, local_restarted, depth_restarted, exit_code, warnings}` | `POST /janus/nat`, `GET /janus/nat/status` |
| **Service restart** (`services_admin.restart_service`) | **sync** | — | — | `ok` only | `RestartResponse{ok, …}` | `POST …/services/…/restart` |

(Plus crash-recovery: node ops `reap_orphans()` → `interrupted`; runtime-apply `recover_on_boot()`
reconciles stuck `applying`/`rolling_back`. NAT sidecar leaves `pending` for the operator to see.)

## The two axes that make a single runner inappropriate
1. **Sync vs async.** Node ops are async by NECESSITY (long SSH provisioning, immune to the response
   lifecycle — the Bug-A reason) → they need a thread + durable journal + `operation_id` + polling +
   orphan reaping. NAT / apply / restart COMPLETE within the request and return a structured result —
   forcing them through an async runner would add a thread + journal + a poll round-trip for no gain
   (and the user's explicit steer: "не делать async там, где достаточно sync + structured status").
2. **History vs current-state.** Node ops + runtime-apply keep a HISTORY (journal records / revisions,
   each id'd). NAT + restart are current-state / fire-and-return. A single store can't serve both
   cleanly without imposing history on the ops that don't want it.

`node_operation_runner` is already the right tool for async/durable; `runtime_config_apply` is already a
rich sync operation. Neither should be replaced (user steer: "не пытаться сразу заменить
node_operation_runner / не загонять все restarts в один generic runner").

## The REAL inconsistency (what actually confuses an operator)
"Did my admin operation succeed, and if not, where did it fail?" gets FOUR different answers:
`{status: succeeded|failed, last_error}` (node) · `{outcome: applied|rolled_back|…, detail}` (apply) ·
`{ok, failure_stage, applied-flags}` (NAT) · `{ok}` (restart). Different SUCCESS words
(succeeded/applied/ok), different FAILURE representations (last_error / detail / failure_stage), different
read surfaces. THAT is the inconsistency worth closing — not the executors.

## Recommendation (D1 — gate)
- **(A) Shared operation-result CONTRACT, no shared executor [LEAN].** A small
  `app/application/operations/` contracts module: a canonical `OperationStatus`
  (`pending` / `running` / `succeeded` / `failed`, with documented domain synonyms — apply's
  `applied`→succeeded, restart's `ok`→succeeded/failed, node's terminal set) + a common result PROTOCOL
  (`status`, optional `failure_stage`, `detail`, `id`). The 4 mechanisms keep their machinery but MAP to
  it (NAT/apply already nearly do; node journal records project trivially). Optionally a unified READ
  view later. Minimal, breaks nothing, directly reduces the operator-facing inconsistency.
- **(B) Build `AdminOperationRunner`** as a shared executor the sync ops route through. Bigger; risks
  imposing async/history semantics on ops that don't want them; little real gain over (A) for the read
  model. Reject as the first step.
- **(C) Do nothing — document only.** The 4 vocabularies persist; cheapest but leaves the inconsistency.

## Plan — assuming (A), sub-commits (tests-first)
- **8A (this)** — recon + this design note. No code.
- **8B** — `app/application/operations/contracts.py`: `OperationStatus` + the result protocol + a
  small `to_operation_status()` mapping per mechanism. Adopt it in the NEWEST/cleanest op first (NAT:
  map `NatUpdateResult`/sidecar onto the shared status; optionally add an `operation_id`). NO change to
  node_operation_runner's storage or runtime-apply's Outcome — only an additive projection to the shared
  vocabulary. A fitness guard that new admin-operation results expose the canonical status. Suite green.

## Open decisions to gate (GO before any code)
- **D1 — shared contract (A) vs runner (B) vs doc-only (C).** Lean **(A)**.
- **D2 — canonical status set:** `{pending, running, succeeded, failed}` (+ documented synonyms) vs also
  modeling `interrupted` / `rolled_back` as first-class. Lean: 4 core + synonyms (keep it small; map the
  recovery states to `failed` with a `detail`).
- **D3 — does NAT get an `operation_id` + history in 8B,** or stay current-state with just the shared
  status word? Lean: shared status word now; `operation_id`/history only if a concrete need appears.

## Red lines (incl. the user's explicit steer)
Don't replace `node_operation_runner` or fold all restarts into one generic runner. Don't touch
`recovery_executor` / FDIR or `task_registry`. Don't async-ify a sync op. No abstraction without a
concrete endpoint behind it. Generalize ONLY where it reduces real inconsistency (the read model), not
for symmetry. Tests-first; full non-e2e suite green per sub-commit.

## Status — DONE (2026-06-21), scope (A)
Decisions: **D1=(A)** shared result contract, NO executor; **D2** canonical set = `{pending, running,
succeeded, failed}` + documented synonyms (recovery states → FAILED, in-flight `applying`/`rolling_back`
→ RUNNING); **D3** NAT gets the shared status WORD now, no `operation_id`/history.
- **8A** — this recon + design note.
- **8B** — `app/application/operations.py` (single module, not a package — no need): `OperationStatus`
  (4 canonical) + `canonical_status()` (domain→canonical, fail-CLOSED to FAILED on unrecognized) +
  `status_from_ok()` + `KNOWN_DOMAIN_STATUSES`. Adopted in NAT first: `NatUpdateResult.operation_status`
  property + `GET /janus/nat/status` and the structured 500 body now carry the canonical
  `operation_status` alongside the domain `status`. NO change to `node_operation_runner` storage or
  runtime-apply's `Outcome`/store — they keep their machinery; the vocabulary is the shared read model.
  Fitness guard **#24** cross-checks that every `runtime_revision_store.STATUS_*` is mapped (the richest
  producer) — it immediately caught a missing `rolling_back` mapping during the build (proof of teeth).
  Contract tests in `tests/test_operations_contract.py`. **24 fitness guards.**

**Deliberately NOT done (deferred):** projecting the node-op journal + runtime-apply onto a UNIFIED read
endpoint; giving NAT an `operation_id` + history; any shared executor. These are only worth it if a
concrete need appears — the vocabulary is the minimal step that closes the operator-facing inconsistency.
