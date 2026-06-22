# ADMIN_CONFIG_DASHBOARD_CLEANUP — Cycle 11 / D6 recon (GATED, no code yet)

D6 was framed as "the last large route-level orchestration node." The recon's honest finding up front:
**that orchestration was already extracted by prior C-04 (Phases 3A/3B) + Phase-5 work** — both route
files are now thin adapters over `app/application` use-cases. The audit concern predates that work. The
only residual is structural-consistency nits (6 inline DTOs), not orchestration. No code until GO.

## Inventory (verified 2026-06-21)
- **`app/routes/admin_dashboard.py` (385 lines, 18 endpoints)** — services / mountpoints / realsense+v4l2
  / encoder / provision / audit-log / soak / dashboard. Every handler is a THIN adapter: `create_mountpoint`
  → `mountpoint_admin.create_mountpoint(req)`; `dashboard_snapshot` → `dashboard_uc.snapshot()`;
  `list_services`/`restart_service` → `services_admin`; encoder → `application.encoder_admin`; devices →
  `application.device_inventory`; `get_audit_log` → `audit_view.read_audit_tail`. It imports from **8+
  application use-cases** (services_admin, encoder_admin, provision_stream, device_inventory,
  mountpoint_admin, audit_view, dashboard). The 385 lines are ~18 small handlers + extensive
  "X moved to application/services (C-04 Phase 3A/3B)" migration-archaeology comments.
- **`app/routes/admin_config.py` (164 lines, 8 endpoints)** — snapshot / reveal / rotate / set /
  detect-public-ip / set-nat-mapping / apply. Thin route→service adapters: `reveal/rotate/set` →
  `secret_store`; `detect_public_ip` → `public_ip.detect`; `set_nat_mapping` → `jcfg_renderer.render`;
  `apply` → `config_apply.apply` (the Cycle-2 boundary). Small handlers (~10–15 lines each).

## Guard alignment (already satisfied)
No `subprocess` / `systemctl` / `sudo` / `httpx` / file-write in either route body (the markers found are
COMMENTS about what moved + imports of the boundary modules `systemd`/`jcfg_renderer`). Route-purity
guards #6 (no infra primitives in routes) and #10 (no durable file writes in routes) already pass. Routes
calling services directly (`secret_store`, `public_ip`) is allowed (CONTRACT.md). The orchestration the
audit feared is in `app/application/*`, FastAPI-free (guard #12).

## Classification (the steer's A–G)
- **A — HTTP route only (the bulk).** ~24 of 26 handlers are thin adapters: call a use-case/service, map
  domain errors → HTTP. Nothing to extract.
- **A (route→service adapter).** The admin_config handlers add a little response-shaping + one inline IP
  validation (`set_nat_mapping`) — small, route-appropriate, not orchestration.
- **Residual structural nit — 6 INLINE DTOs** that didn't move to `app/application` contracts like the
  others did: `ProvisionStreamRequest` / `ProvisionStreamResponse` (admin_dashboard) and `RotateResponse`
  / `SetFieldRequest` / `DetectIpResponse` / `RevealResponse` (admin_config). Most DTOs already live in
  application (imported); these 6 are the inconsistency.
- **B/F — migration-archaeology comments.** Extensive "moved to … (C-04 …)" notes. Documentation of past
  moves — the trail has value; removing is cosmetic. LEAVE.
- **G — dead code:** none found (the C-04/Phase-5 moves left only forwarding comments, not dead defs).

## Importer + patch-anchor map (the churn surface is tiny)
- DTO importers in tests: only `test_encoder_admin.py:202` imports `ad.ProvisionStreamRequest`. The 4
  admin_config DTOs are NOT imported by any test.
- Route patch anchors in tests: only `admin_dashboard.restart` (1). Negligible.

## The honest conclusion + minimal cut (D1 — gate)
The "large route-level orchestration cleanup" is **substantially DONE** (C-04 + Phase-5). There is NO big
route rewrite to do. The only structural-consistency improvement:
- **(A) Move the 6 inline DTOs to their application contracts** — `ProvisionStreamRequest/Response` →
  `app/application/provision_stream` (next to `provision_stream` use-case); the 4 config DTOs →
  `app/application/config_view` (where the config read-model already lives). Routes import them like all
  the other DTOs. Low risk; churns 1 test (`ProvisionStreamRequest`) + the route imports. A consistency
  win, not a correctness one — honest about that.
- **(B) Declare D6 substantially-done; skip the DTO move** — the routes are already thin; 6 inline DTOs
  are a tiny nit not worth a cycle. Move to a higher-value backlog item (camera/session state model).
- **(C) Trim the archaeology comments** — cosmetic; rejected (loses the migration trail).

## Explicit DO-NOT-TOUCH
The thin handlers (don't "re-orchestrate"); admin auth + rate-limit deps; the `config_apply.apply`
boundary (Cycle 2); the `application/*` use-cases; the migration-archaeology comments; any unrelated
route. No generic dashboard Manager/Provider. No API response-shape change without a characterization test.

## Open decisions to gate (GO before any code)
- **D1 — (A) move the 6 inline DTOs / (B) declare done & skip / (C) trim comments.** Lean: **(A) if you
  want the consistency closed cheaply, else (B)** — this is genuinely marginal; the routes are already
  thin. No correctness driver either way.
- **D2 — guard?** A guard "no route defines a request/response BaseModel inline" would lock the
  DTOs-live-in-application pattern, but it'd need an allowlist for the many legit small response models
  across routes → likely decorative (Cycle-6 honesty). Lean: **no guard.**

## Status — CLOSED as already-done (2026-06-21), scope (B)
Decision **D1=(B)**: declare D6 substantially-done; do NOT move the 6 inline DTOs. The recon established
that the orchestration the audit feared was already extracted by C-04 (Phases 3A/3B) + Phase-5 — both
route files are thin adapters over `app/application` use-cases, and the route-purity guards (#6/#10) +
FastAPI-free application rule (#12) already hold. The only residual (6 inline DTOs + archaeology
comments) is a cosmetic consistency nit, not worth a cycle. **No production code change.** This recon is
the artifact: it records WHY D6 needs no work, so a future audit doesn't re-open it.

**Explicitly deferred (only if a concrete need appears):** moving `ProvisionStreamRequest/Response` +
the 4 admin_config DTOs into their `app/application` contracts (a pure consistency move; churns 1 test).
No guard (a "no inline route BaseModel" guard would need a broad allowlist → decorative).

**D6 is closed.** Next: the camera/session desired-vs-actual state model (higher value than this nit).

## Red lines (incl. the steer)
Don't touch unrelated routes. No API response-shape change without a characterization test. No FastAPI in
`application/`. No generic dashboard manager/provider. Don't break admin auth/rate-limit. Don't weaken a
guard. Don't move code for symmetry beyond the one DTO-consistency cut (if taken). Tests-first; full
non-e2e suite green per sub-commit.
