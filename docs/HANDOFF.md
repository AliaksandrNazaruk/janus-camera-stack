# HANDOFF — start here (janus_camera_page)

A new-team entry point. The repo has thorough docs (16 in `docs/`, 44 design notes, a `CONTRACT.md`);
this file is the **map** so you don't have to read them in the dark. Read this, then follow the pointers.

## 1. What this is (60 seconds)
`janus_camera_page` is the **L4 control plane** for a Raspberry-Pi camera gateway. It is a FastAPI
service (port 8900) that manages camera streams end-to-end: it owns the *desired* topology (which sensors
/ remote nodes stream where), drives Janus (WebRTC SFU, L3) + encoders (L2) + the camera hardware (L0)
toward that desired state, and serves an operator dashboard + admin API. It does NOT transcode media —
it orchestrates. Layer boundaries: L4 (this) → L3 Janus (`janus-admin` CLI) → L2 encoders
(`encoder-admin` CLI) → L0 cameras (`camera-admin` CLI). See `docs/CONTRACT.md` for the full contract.

## 2. The safety net — 25 executable architecture guards (read this first)
The most important thing to know: **the architecture is enforced by code, not by convention.**
`tests/test_architecture_fitness.py` holds **25 fitness guards** (`#1`–`#25`) that run in the normal
suite. They make whole classes of regression *impossible to merge*:
- routes stay thin (no `subprocess`/`systemctl`/`httpx`/file-writes in `routes/**` — #6/#10),
- `application/**` is FastAPI-free (#12),
- stores fail CLOSED on corruption (#18), destructive systemd goes through the scoped CLI (#19),
- long-lived async tasks only via `task_registry` (#21), `stream_bindings` stays a cohesive package (#22),
- settings have one owner (#25), the runtime-config + NAT operation surfaces stay consistent (#20/#23/#24).

**Implication for you:** run `pytest tests/ --ignore=tests/e2e`. If it's green, you have not broken the
architecture. To change a guarded invariant you must change the guard *deliberately* — that's the signal.

## 3. Where things live (the layering)
```
app/routes/**        thin HTTP adapters — parse request, call a use-case, map domain errors → HTTP
app/application/**    FastAPI-free use-cases (the orchestration) — return results/raise domain errors
app/services/**       adapters + stores + clients (Janus REST, *-admin CLIs, state files, probes)
app/core/**           settings (the config owner), admin/viewer auth, lifespan/events, session_store
app/config/**         PORTS / DEVICES network constants (the one place for them)
camera_bringup/       separable L0 tooling, reached ONLY via the camera-admin CLI (no imports)
```
The flow for a mutating endpoint: `route → application use-case → services (CLI/store/probe)`. The
heavy logic is in `application/` (e.g. `application/stream_bindings/`, `application/janus_nat/`),
FastAPI-free and unit-tested.

### The state model (how to reason about "is it running?")
L4 is desired-vs-actual (no single state machine — per-domain reconcilers). The contract, in one line:
**act on `desired_active` (intent) and on the probes / `reconcile_drift` (actual) — NOT on
`StreamBinding.status`** (that's stored last-known/intent, can be stale). Full map:
`docs/CONTRACT.md` § "State model: desired vs actual" + `docs/design/CAMERA_SESSION_STATE_MODEL.md`.

### Nodes (local + remote) — read `docs/NODE_CONTRACT.md`
A node is a node; **transport is the only difference** between cam10 (local) and a remote node.
`desired_up` (Start/Stop) is SEPARATE from `fdir.enabled` (escalation); nodes do NOT autostart
encoders — the **gateway** converges every `desired_up` stream when the node is reachable. Provision /
start / stop / reboot-recovery runbook + invariants: **`docs/NODE_CONTRACT.md`** (locked by guard #28).

## 4. Two campaigns got it here (the design-note trail)
1. **Layering campaign (D1/D2/D3)** — moved it from "fat-route + god-store + FastAPI-everywhere" to
   `routes → application → services`. Story: `docs/ARCHITECTURE_CAMPAIGN_CLOSEOUT.md`; live anchor:
   `docs/ARCHITECTURE_CURRENT.md` ("if a doc disagrees with this file, this file wins").
2. **Production-risk hardening campaign (Cycles 1–12, guards #18–#25)** — closed real failure modes +
   structural debt, each behind a guard or a documented decision. Index by theme:

| theme | design note(s) | guard |
|---|---|---|
| store fail-closed safety | `SECRET_CONFIG_STORE_SAFETY.md` | #18 |
| service-control boundary | `SERVICE_CONTROL_CONSISTENCY.md` | #19 |
| runtime-config truth + apply | `RUNTIME_CONFIG_TRUTH.md`, `B2_RUNTIME_CONFIG_APPLY.md` | #20 |
| tracked background tasks | `TRACKED_BACKGROUND_TASKS.md` | #21 |
| stream_bindings route package | `STREAM_BINDINGS_ROUTE_SPLIT.md` | #22 |
| NAT/TURN operation boundary | `JANUS_NAT_OPERATION_BOUNDARY.md` | #23 |
| admin-operation vocabulary | `ADMIN_OPERATION_MODEL.md` | #24 |
| settings ownership | `SETTINGS_OWNERSHIP.md` | #25 |
| services de-dup / nat_config | `SERVICES_DECOMPOSITION.md`, `NAT_CONFIG_SPLIT.md` | — |
| admin routes / state model | `ADMIN_CONFIG_DASHBOARD_CLEANUP.md`, `CAMERA_SESSION_STATE_MODEL.md` | — |

(The full 44-note set is `docs/design/`; the desired/actual reconcile model also has the older
`DESIRED_ACTUAL_RECONCILE_MODEL.md` + `UNIFIED_FDIR_OVER_STREAM_BINDINGS.md`.)

## 5. How to make a change safely (the protocol the campaign used)
Every cycle followed this, and it works:
1. **recon-only** — find the exact files + the real failure mode; write a design note in `docs/design/`.
2. **gate the decisions** — list the choices; get a GO before any code (don't guess at intent).
3. **characterization tests first** — pin current behavior; for a moved symbol, re-point the patch at the
   new source with identical assertions (the diff is the audit trail).
4. **minimal code change** — behavior-preserving unless deliberately changing it.
5. **a fitness guard** — *only if it locks a real invariant with teeth*; a decorative guard is worse than
   none (several cycles correctly added none).
6. **full non-e2e suite green per sub-commit**, one commit per sub-step.
Recurring lesson: **recon-first repeatedly stopped premature refactors** — three of the last cycles found
the work was already done or the remainder wasn't worth the churn. Prefer the smallest correct cut.

## 6. Current state + open backlog
- **Suite:** `pytest tests/ --ignore=tests/e2e` green; 25 fitness guards green. Branch:
  `refactor/consolidate-camera-stack`.
- **Review snapshot:** `AUDIT_CONTEXT.md` (root) + the latest `_archive/..._review_source_post_cycleN`
  tarball is the reviewer-facing checkpoint (built secret-free via `scripts/build_release_archive.sh`).
- **Backlog (bounded, recon-first + gated, none critical):** local cam10 drift parity with remote
  (`reconcile_drift` for local); the depth-node NAT read-path silent fallback; a Janus-session orphan
  reaper; an admin inline-DTO move (cosmetic); a further `nat_config` split (cohesive — deferred);
  `AdminOperationRunner` (deferred — the shared `OperationStatus` vocabulary already covers the read model).
  See `docs/KNOWN_LIMITATIONS.md` + the design notes' "deferred" sections.

## 7. Key docs (pointers, by need)
- **Contract / boundaries:** `docs/CONTRACT.md` — what L4 owns, the L3/L2/L0 boundaries, the state model.
- **Current architecture truth-map:** `docs/ARCHITECTURE_CURRENT.md`.
- **Reviewer snapshot of closed risks:** `AUDIT_CONTEXT.md` (root).
- **Run / deploy:** `docs/INSTALL.md`, `docs/DEPLOYMENT.md`, `docs/OPERATOR_RUNBOOK.md`.
- **A specific decision:** the matching `docs/design/*.md` (named by theme above).
