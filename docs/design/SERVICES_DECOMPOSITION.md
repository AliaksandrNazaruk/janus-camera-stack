# SERVICES_DECOMPOSITION — Cycle 6 recon + plan (GATED, no code yet)

Audit finding: `app/services` is a "mixed bucket" (63 flat modules, ~9.9k LOC). Cycle 6 asks what — if
anything — to decompose. The recon's honest conclusion up front: **this is NAVIGABILITY debt, not
correctness debt.** There is no fat-route-style smoking gun; the modules are mostly individually
cohesive. So the right Cycle 6 is SMALL and targeted, not a mass regrouping (which would be exactly the
"перетаскивание ради красоты" noise to avoid). No code until GO.

## Recon — the landscape (verified 2026-06-21)
63 flat `app/services/*.py` (~9925 LOC) + 2 ALREADY-decomposed subpackages (`sensor_lifecycle/`,
`stream_binding_store/`) — the precedent for a package split. Most files are single-purpose. They fall
into clear de-facto FAMILIES already grouped by name (just not by directory):

| family | n | modules |
|---|---|---|
| `recovery_*` | 5 | ladder (397) / executor (270) / persistence (158) / policy (99) / state_machine (79) |
| `runtime_*` | 5 | revision_store (327) / validator (253) / apply (239) / builder (137) / env_writer (197) |
| `*_proxy` | 6 | ws / depth_mux / janus / depth_camera / realsense_mux / relay |
| `*_admin` | 3 | encoder / janus / janus_dashboard |
| `node_*` | 4 | provisioner / client / transport / operation_runner |
| `realsense*` | 3 | catalog / probe / mux_proxy |
| `fdir_*` | 2 | events / quiesce |

### Concern classification (mixed-layer scan)
A broad concern-marker scan (EXEC=subprocess/sudo, STORE=persistence, THREAD=loops, HTTP, FASTAPI,
POLICY, ENVCFG) flags candidates; inspecting the top ones separates *genuinely mixed-layer* from
*cohesive-but-busy*:
- **`nat_config.py` (274) — the ONE genuinely cross-layer-mixed module.** It does FIVE layers: the
  `JanusNatConfig` model + `load/save` (store) + `generate_turn_credentials` (TURN cred-gen) +
  `render_nat_block`/`patch_janus_cfg_with_nat` (jcfg templating) + `restart_janus`/
  `restart_depth_camera_janus` (service control — and `restart_janus` is a **raw**
  `subprocess.run(["sudo","/usr/local/bin/janus-admin","restart"])`, the deferred D3 call-style).
- **`recovery_ladder.py` (397) — big but COHESIVE** (the FDIR ladder) and part of the `recovery_*`
  family; it's the safety-critical recovery core (10 importers). Not "mixed-harmful"; high-risk to
  touch. Leave it (consistent with the Cycle-4 red line: don't resurger FDIR internals for cosmetics).
- **`jcfg_renderer` / `node_client`** — flagged by the broad scan but actually cohesive (a renderer; a
  client-adapter family). Not targets.

### What's NOT wrong
No services module is a correctness hazard from its mixing. The store-safety (C1), service-control (C2),
runtime-config-truth (C3), and task-ownership (C4) boundaries are already enforced by guards #18–#21.
A mass regroup into subpackages would touch dozens of importers (e.g. `recovery_ladder` alone has 10)
for a navigability-only gain — high churn, low correctness value, and against the "no cosmetics" steer.

## The minimal first-cut options (D1 — gate this)
- **(A) D3 raw-CLI call-style consolidation [LEAN].** Route the raw `system.run/subprocess([sudo,
  /usr/local/bin/<x>-admin, ...])` call-style in `nat_config` (restart_janus), `mode_enforcer`,
  `color_config`, `sensor_tuning_env` through the existing `encoder_admin.invoke` / `janus_admin`
  adapters (the scoped admin-CLI ports). Bounded, **safety-adjacent** (continues the Cycle 1–4 boundary
  theme), existing adapter pattern, and it ADDS a guard ("no raw sudo `*-admin` subprocess in services
  outside the admin adapters"). LOW risk, real value, and it de-mixes the worst part of nat_config as a
  side effect. This is the truest continuation of the campaign.
- **(B) Targeted split of `nat_config.py`** along its 5 seams (model+store stays; cred-gen → a TURN
  module; render → jcfg_renderer; restart → service-control/admin adapter). The one genuine mixed
  module. MEDIUM risk (it touches live Janus NAT/TURN config) + medium value.
- **(C) Group a family into a subpackage** (e.g. `recovery_* → app/services/recovery/`) for
  navigability, like `sensor_lifecycle/`. **Recommend AGAINST as the first cut** — ~22 importer edits
  across a safety-critical family for a cosmetic gain; this is the noise the steer warns against.

## Plan — assuming (A), sub-commits (tests-first, suite green between)
1. **char/re-point** — pin the current call sequence of each raw-CLI site (the tests that assert the
   `subprocess.run([... -admin ...])` argv), then re-point to assert the adapter call (identical
   unit/action/timeout/rc semantics), RED-until step 2.
2. **route through the adapter** — `restart_janus` etc. → `janus_admin.invoke("restart")` /
   `encoder_admin.invoke(...)`; behavior-preserving (same CLI, same scoping, same error→RuntimeError).
3. **guard** — fitness guard **#23**: no raw `subprocess([... "sudo", "/usr/local/bin/*-admin" ...])`
   in `app/services/**` outside the admin-adapter modules (the call-style boundary). Locks it.

## Open decisions to gate (GO before any code)
- **D1 — first cut: (A) D3 call-style / (B) nat_config split / (C) family package.** Lean **(A)**.
- **D2 — guard shape (if A):** ban raw sudo `*-admin` subprocess in services/** outside the adapter
  allowlist (encoder_admin / janus_admin / system primitive). Lean: yes.
- **D3 — scope (if A):** which sites — nat_config + mode_enforcer + color_config + sensor_tuning_env, or
  a subset for the first cut. Lean: do the clean ones first; defer any that need an adapter feature the
  invoke port doesn't model yet (e.g. color_config's `--instance color` + custom timeout, per Cycle 2).

## Deeper recon during build (D1=A GO'd) — the premise is messier than the recon claimed
On inspecting the actual call sites + adapters, the "route raw CLI through the existing adapter" framing
does NOT yield a clean minimal cut. Findings (verified 2026-06-21):
- **9 modules** hold raw `["sudo","/usr/local/bin/<x>-admin",...]` argv: `encoder_admin`,
  `sensor_lifecycle/encoder_admin`, `system`, `mode_enforcer`, `nat_config`, `color_config`,
  `sensor_tuning_env`, `recovery_executor`, `v4l2`.
- **TWO encoder-admin adapters with mismatched semantics**: `services/encoder_admin.invoke()` (direct
  `subprocess`, `sudo -n`, returns `(rc, stderr)`, no raise on rc) vs `sensor_lifecycle/encoder_admin`
  (uses `system.run`, `sudo`, RAISES `LifecycleError`). The raw callers use `system.run` (sudo, raises) —
  so neither adapter is a drop-in: routing through `invoke` flips `sudo`→`sudo -n` and the raise-path.
- **`janus-admin` CLI has NO adapter** (`services/janus_admin.py` is the HTTP Janus admin API, unrelated)
  → nat_config's `janus-admin restart/nat-config` can't route anywhere.
- Several sites are **bare `encoder-admin <action>` with NO `--family`** (mode_enforcer, system, recovery)
  — a different semantic the `--family`-required adapters can't model.
- `recovery_executor` is FDIR-critical (Cycle-4 red line: don't touch for cosmetics); `v4l2`'s
  camera-admin calls are cohesive in one module.

**Consequence for the guard (D2):** a meaningful "no raw `*-admin` argv outside the adapters" guard needs
a SMALL allowlist — but 7 of the 9 sites can't migrate cleanly (no adapter / different semantics / FDIR).
Allowlisting all 9 makes the guard toothless; migrating all 9 is the messy all-in job the steer warns
against. So D3 does NOT decompose into "small clean cut + meaningful guard."

**The one genuinely clean win:** `color_config._restart_color_encoder` and `sensor_tuning_env.write_tuning`
are the SAME pattern duplicated — `system.run(["sudo",".../encoder-admin","restart","--family",
"rs-stream","--instance",<X>], timeout=N)` after a tuning-env write. That duplication is worth removing via
ONE shared helper (behavior-preserving — same primitive/sudo/raise), independent of the broader guard.

### Refined options (re-gate)
- **(A1) Narrow de-dup only** — collapse the two `rs-stream@<instance>` restart sites onto one shared
  encoder-admin restart helper (same `system.run`/sudo/raise semantics → behavior-preserving); re-point
  their tests; NO global guard (it would be toothless given the 7 un-migratable sites). Small, real
  (removes duplication), low-risk. Honest about what it is.
- **(A2) Defer D3** — accept that the raw-CLI call-style isn't a clean cycle; the sites ARE all scoped
  CLIs already (privilege boundary intact), this is a style nit. Pick another backlog item or hold.

## Red lines
No mass file-moving for aesthetics. Behavior-preserving: same CLI invoked, same scoping, same
error mapping. Don't touch the FDIR recovery internals (recovery_ladder/executor) for cosmetics. Don't
broaden what any admin CLI accepts. Tests-first; never weaken an assertion. Full non-e2e suite green per
sub-commit. Whatever is chosen stays SMALL — one bounded cut, then re-assess.

## Status — DONE (2026-06-21), scope (A1)
After the deeper recon showed D3 doesn't yield a clean cut + meaningful guard, the gated choice was
**(A1) narrow de-dup only** (no global guard — it would be toothless given the 7 un-migratable raw-CLI
sites). One commit:
- `app/services/encoder_admin.py` — added `restart_unit(family, instance, *, timeout)`: the shared home
  for the post-tuning-write encoder restart. Uses `system.run` (`sudo`, raises RuntimeError) — NOT
  invoke()'s `sudo -n`/tuple — so the callers' domain-error mapping AND their `app.services.system.run`
  test patch-point are preserved verbatim.
- `app/application/camera/color_config._restart_color_encoder` and
  `app/services/sensor_tuning_env.write_tuning` → call `encoder_admin.restart_unit("rs-stream", <X>,
  timeout=60|20)` instead of each hand-rolling the identical `["sudo", ".../encoder-admin", "restart",
  "--family", "rs-stream", "--instance", <X>]` argv. `system` import dropped from both.
- 2 char tests added (`restart_unit` argv + RuntimeError propagation). **ZERO test churn** on the call
  sites — they patch `app.services.system.run` at the source + assert the argv, both unchanged.

**Result:** the duplicated rs-stream@<instance> restart now has ONE home (the encoder-admin adapter);
behavior identical (same CLI, sudo, raise-path, timeouts). **Explicitly NOT done** (acknowledged, not
silently dropped): the bare-no-family encoder-admin sites (mode_enforcer/system), the no-adapter
`janus-admin` CLI in nat_config, the FDIR `recovery_executor` sites, and `v4l2`'s camera-admin — these
are scoped CLIs already (privilege boundary intact); migrating them is a larger job with mismatched
adapter semantics, deferred deliberately. No fitness guard this cycle (would be toothless).

## Deferred backlog (services)
- `nat_config.py` split (option B) — the one genuine 5-layer mixed module, if/when its Janus NAT/TURN
  config churn is worth the medium risk.
- Family subpackaging (recovery_/runtime_/*_proxy) — navigability only; do ONLY if it stops paying for
  itself as flat files. Not now.
