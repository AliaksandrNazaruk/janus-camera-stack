# Track A — Settings Relocation + Call-Time Reads (Design Spec v2)

**Status:** DESIGN ONLY — **v2, adversarial-review-corrected.** No code, no IaC change applied, no service mutation.
**Scope:** unblock the B2 `NEW_SESSIONS_ONLY` apply class (B2 C1/C2) by making `ice_policy` + `turn_cred_ttl` runtime-applyable — relocate them out of the systemd `Environment=` directive into a writable, non-secret env file, and refactor the two `Settings` fields to **fail-safe** call-time reads.
**Date:** 2026-06-18 · **Prerequisite for:** B2 apply.
**Decisions (operator):** env file = `/etc/robot/rs-runtime.env` · scope = all surfaces *(honestly: per-surface, see §7)* · seed `TURN_CRED_TTL` too.

> v1 was grounded but a 2-reviewer adversarial pass (7 attack angles) found two
> CRITICALs and several HIGHs. §0 is the corrections changelog. The activation
> mechanism (`default_factory` + `cache_clear`) was **empirically proven** to refresh
> both fields; the defects are in *deploy reachability, fail-safety, and surface honesty*,
> all fixed below.

---

## 0. v2 corrections (adversarial review)

| # | v1 claim | Reality (grounded) | v2 resolution |
|---|---|---|---|
| **TA-C1** | edit the repo `override.conf`, then `daemon-reload`+restart | **No automation deploys the drop-in.** `install.sh` writes its *own* base unit via heredoc (install.sh:695,716) and has zero `.service.d`/`override.conf` references; no script copies `infrastructure/.../systemd/`. The live drop-in was placed by a manual `cp -r` (DEPLOYMENT.md:115). A repo edit never reaches the Pi → live `Environment=ICE_POLICY=relay` persists and shadows the file → **silent total no-op**. | §4: the relocation **owns its deploy step** (install.sh installs the drop-in) and acceptance asserts against the **live unit** (`systemctl show`), not the repo file. |
| **TA-C2** | `int(os.getenv("TURN_CRED_TTL","3600"))` field | **Malformed-value DoS.** The file is now operator-writable; a stray space / inline `# comment` / `3600s` throws `ValueError` from inside `get_settings()`, which *every* route calls (60+ sites) → whole service 500s, `readyz` 503s → orchestrators evict. `cache_clear()` cannot self-heal (bad value stays in `os.environ`). Empirically reproduced. | §5: **fail-safe `_int_env`/`_str_env`** parsers (try/except → log + default; `ice_policy` allowlisted to `{all,relay}`). Lands *with* the relocation. |
| **TA-C3** | "relay stays relay" + "seed all surfaces" | **Contradiction.** compose/k8s/helm set `ICE_POLICY` *nowhere* → default `"all"`. Seeding `relay` everywhere would force relay-only on cloud deployments (latency/cost regression; `relay` is the depth double-NAT special case, not a cloud default). | §4/§7: **surface-aware seeding.** "relay stays relay" means *each surface keeps its own current value*: systemd seeds `relay`; compose/k8s seed `all` (or aren't seeded). |
| **TA-C4** | first deploy is safe | **Regression window.** `EnvironmentFile=-` (optional) → if the directive is removed before the file exists, `ICE_POLICY` is **unset** → `"all"`, flipping color nodes off relay silently. | §4: **ordering invariant** — seed the file (and install the drop-in) in the *same* action that removes the directive; acceptance checks a **color** node post-deploy. |
| **TA-C5** | k8s "persistence gap" (benign) | **Split-brain.** A live `os.environ`+`cache_clear` apply with no ConfigMap update: pod serves `relay`, ConfigMap says `all`, `/status` reports `relay` (looks persisted), next rollout (`strategy: Recreate`, 20-camera-page.yaml:15) silently reverts. No detector. | §7.2: k8s apply must **either** patch the ConfigMap in the same apply **or** return a loud `NOT_PERSISTED` + raise a drift flag in `/status`. Deferring persistence while shipping live apply is the dangerous half. |
| **TA-C6** | helm covered by "all surfaces" | **Gap + name drift.** Helm ships its own ConfigMap on the *legacy* env-name regime (`CAMERA_TYPE`/`JANUS_API_URL`, aliases per settings.py:45-51) and has no `icePolicy` value. "All surfaces" is provably untrue there. | §7.3: helm **explicitly scoped OUT** of Track A (tracked separately), so the scope claim is honest; pre-existing legacy/canonical drift noted. |
| **TA-C7** | seed "like the secrets seeding" | **Ambiguous.** install.sh has *both* guarded (secrets, :731 `if [ -f ]`) and **unguarded** (example envs :653-658 `install -m`, base unit :716 `mv`) patterns. Copying the wrong idiom clobbers an operator-tuned file on upgrade. | §4: the design **shows** the exact `if [ ! -f ]` guard; §10 adds a double-run negative test. |
| **TA-C8** | compose: add `env_file` to prod + dev | `docker-compose.dev.yml` has **no** `janus-camera-page` service (dev runs it on host via systemd, .dev.yml:11-13). prod has no writable bind mount; `env_file` is read at *create* time, not on `docker restart`. | §7.1: drop the `.dev.yml` target; prod needs an explicit writable bind mount + `up -d`/recreate (same external-write shape as k8s, not the clean systemd reload). |
| **TA-C9** | acceptance test #3: "fresh `get_settings()` reflects" | Test fixtures build `Settings()` **directly** (conftest.py:48), which re-runs `default_factory` regardless → the test passes *vacuously*, not guarding the `lru_cache` path. | §10: the regression guard must go through `get_settings()` + `cache_clear()`, never bare `Settings()`. |
| **TA-C10** | rs-runtime.env "non-secret" | Holds today (B2 §5.4 allowlist blocks secret bleed; 0o644 matches `rs-color.tuning.env`). Residual: the file is world-readable + writable; a hand-added `TURN_PASS=` would leak at the FS before the B2 scan catches it. | §3: **fixed key allowlist** `{ICE_POLICY, TURN_CRED_TTL}`; any other key = deploy error. |
| **TA-C11** | — | The B2-0 capability blocker string (`runtime_revision_store.py:206`, "…provided via systemd Environment=…") becomes **stale** once Track A lands. | §9: updating that blocker text is part of Track A's done-criteria. |

---

## 1. Purpose & Non-Goals

### 1.1 Purpose
Make `ice_policy`/`turn_cred_ttl` runtime-applyable (B2 §11) by removing the two blockers the B2 review found (C1 frozen-literal defaults; C2 systemd-directive injection), **without** introducing the deploy-gap, DoS, or behavior-flip the Track A review found.

### 1.2 Non-Goals
```
No B2 apply code (this makes the two fields apply-CAPABLE only).
No /apply, rollback, FDIR quiesce (Track B).
No secrets relocation (camera-secrets.env stays bind-read-only).
No change to other Settings fields.
No behavior change at deploy time — where "no change" means EACH SURFACE KEEPS ITS
  OWN CURRENT EFFECTIVE VALUE (systemd: relay; compose/k8s: all), NOT "relay everywhere".
No Pi reboot.
```

---

## 2. Grounded reality
(unchanged from v1 — all verified)

| Fact | Evidence |
|---|---|
| `ICE_POLICY=relay` injected **only** via the systemd drop-in `Environment=` line | override.conf:7 (== live) |
| `TURN_CRED_TTL` has no env source — frozen default `3600` | absent everywhere |
| `/etc/robot` is already `ReadWritePaths`; only `camera-secrets.env` is `BindReadOnlyPaths` | override.conf:33,49,52 |
| both fields consumed call-time via `get_settings()`; **no module capture** of them | janus.py:138,151; system.py:279; builder.py:83,90 (only `_CAM_TYPE` is captured — a different field) |
| nothing outside L4 reads either var | grep empty |
| `field(default_factory=…)` already used in the same frozen dataclass | settings.py:177 |
| `default_factory` + `cache_clear()` refreshes **both** fields; old frozen-literal stays stale | **empirically run** (str+int) |
| compose/k8s/helm set `ICE_POLICY` **nowhere** (default `all`); k8s env is read-only `envFrom: configMapRef` | docker-compose.prod.yml:33; 20-camera-page.yaml:34; 10-config.yaml |
| **install.sh does not deploy the drop-in**; live drop-in placed manually | install.sh:695,716 (own heredoc unit, no `.service.d`); DEPLOYMENT.md:115 |

---

## 3. Decisions + the non-secret invariant
1. Env file: `/etc/robot/rs-runtime.env` (non-secret, runtime-tunable).
2. Scope: **per-surface** (systemd fully; compose with caveats §7.1; k8s live-only + drift-flag §7.2; helm OUT §7.3).
3. Seed `TURN_CRED_TTL=3600` alongside `ICE_POLICY` (systemd seed value `relay`).
4. **Invariant (TA-C10):** `rs-runtime.env` is allowlisted to exactly `{ICE_POLICY, TURN_CRED_TTL}`. Any other key is a deploy error (keeps `0o644` justified — no secret can live there).

---

## 4. Change set — systemd (the live Pi, primary)

**The relocation must own its deploy (TA-C1).** Today nothing deploys the drop-in, so v2 adds it to `install.sh`:

```
install_camera_page():
  # 1. SEED FIRST (TA-C4 ordering) — idempotent (TA-C7):
  if [ ! -f /etc/robot/rs-runtime.env ]; then
    install -m 0644 /dev/stdin /etc/robot/rs-runtime.env <<'EOF'
# Non-secret runtime-tunable knobs (writable; loaded by the L4 unit). Allowlist: ICE_POLICY, TURN_CRED_TTL.
ICE_POLICY=relay
TURN_CRED_TTL=3600
EOF
  fi
  # 2. INSTALL THE DROP-IN (was never automated):
  install -d /etc/systemd/system/janus-camera-page.service.d
  install -m 0644 infrastructure/color_node/systemd/janus-camera-page.service.d/override.conf \
                  /etc/systemd/system/janus-camera-page.service.d/override.conf
  # 3. daemon-reload + restart (file already present → no unset window)
```

Drop-in edit (`infrastructure/color_node/systemd/.../override.conf`):
```diff
- Environment=ICE_POLICY=relay
+ EnvironmentFile=-/etc/robot/rs-runtime.env
```
⚠️ Removing `Environment=ICE_POLICY` is mandatory (it shadows `EnvironmentFile=`). `/etc/robot` is already `ReadWritePaths` → the file is runtime-writable with no sandbox change.

> Deploy class = the read-only L4 restart used for B1-3/B2-0 (not a Pi reboot). The seed-before-remove ordering closes the TA-C4 unset window.

---

## 5. Settings refactor (fail-safe — TA-C2)

`app/core/settings.py` — add safe parsers, then point the two fields at call-time reads:
```python
def _str_env(key: str, default: str, allowed: set[str] | None = None) -> str:
    v = os.getenv(key, default)
    if allowed and v not in allowed:
        log.warning("Settings: %s=%r not in %s; using %r", key, v, allowed, default); return default
    return v

def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None: return default
    try: return int(raw.strip())
    except (ValueError, AttributeError):
        log.warning("Settings: %s=%r not an int; using %d", key, raw, default); return default
```
```diff
- ice_policy: str = os.getenv("ICE_POLICY", "all")
+ ice_policy: str = field(default_factory=lambda: _str_env("ICE_POLICY", "all", {"all", "relay"}))
- turn_cred_ttl: int = int(os.getenv("TURN_CRED_TTL", "3600"))
+ turn_cred_ttl: int = field(default_factory=lambda: _int_env("TURN_CRED_TTL", 3600))
```
- `default_factory` re-reads `os.environ` per `Settings()`; **`cache_clear()` stays mandatory** (proven: inert under `@lru_cache` without it).
- Fail-safe means a malformed runtime value **logs and falls back**, never crashing the universally-called `get_settings()` — and also hardens startup (`EnvironmentFile=-` tolerates a missing file but not a malformed line).
- (Optional hardening: give `turn_port`/`turn_tls_port` the same `_int_env` treatment — same latent crash class, settings.py:119,131.)

---

## 6. Activation model (what this unblocks for *future* B2 apply)

**Live effect — universal:** `os.environ[KEY]=value → get_settings.cache_clear() → verify build_effective()`. Process-local; identical on every surface.
**Persistence — per-surface:** systemd → write `rs-runtime.env` (read-merge-write atomic); compose → write the bind-mounted file + recreate (§7.1); k8s → ConfigMap-API patch + rollout (§7.2). The split is real (§7) and must not be papered over.

---

## 7. Per-surface honesty (the "all surfaces" reality)

### 7.1 compose (TA-C8)
- `docker-compose.dev.yml` has **no** `janus-camera-page` service (dev = host systemd) → **not a target**.
- `docker-compose.prod.yml`: add `env_file: [/etc/robot/rs-runtime.env]` **and** a writable bind mount for it (the container must be able to write it to persist). Reload requires `compose up -d`/recreate (NOT `docker restart` — `env_file` is read at create time). Seed value `ICE_POLICY=all` (preserve current default — TA-C3).

### 7.2 k8s (TA-C5)
`ICE_POLICY`/`TURN_CRED_TTL` absent from `camera-page-config` (default `all`). Live apply mutates `os.environ`+`cache_clear` in-process. **Because `envFrom: configMapRef` is read-only and the Deployment is `strategy: Recreate`, a live-only apply is a split-brain that the next rollout silently reverts.** v2 rule: a k8s apply MUST either (a) patch the ConfigMap in the same apply (persistence non-deferred for k8s), or (b) return a loud `persisted:false, warning:"patch ConfigMap camera-page-config or this reverts on pod restart"` AND raise a config-drift flag surfaced by `/status`. No silent live-only apply.

### 7.3 helm (TA-C6) — SCOPED OUT
The helm chart ships its own ConfigMap on the **legacy** env-name regime (`CAMERA_TYPE`/`JANUS_API_URL`, deprecated aliases per settings.py:45-51) and has no `cameraPage.icePolicy` value. Making it consistent is a separate change (and would also want to resolve the pre-existing legacy↔canonical name drift). **Helm is explicitly out of Track A.** Stating this keeps the scope claim honest rather than silently false.

---

## 8. Adversarial risks (carried into the change)
```
R-A1  Precedence trap: Environment=ICE_POLICY not removed → file shadowed, no-op.        (§4)
R-A2  cache_clear omitted: default_factory inert under @lru_cache.                        (§5)
R-A3  DEPLOY GAP (TA-C1): repo edit never reaches the Pi unless install.sh installs the
      drop-in AND acceptance asserts the LIVE unit, not the repo file.                    (§4/§9)
R-A4  MALFORMED-VALUE DoS (TA-C2): unguarded int()/str → get_settings() crashes app-wide;
      cache_clear can't self-heal. Fail-safe parsers mandatory.                            (§5)
R-A5  SURFACE BEHAVIOR FLIP (TA-C3): seeding relay on compose/k8s forces relay-only.
      Seed each surface's CURRENT value (systemd relay, cloud all).                        (§7)
R-A6  k8s SPLIT-BRAIN (TA-C5): live-only apply reverts on rollout, no detector.            (§7.2)
R-A7  Unset window (TA-C4): remove directive before file exists → ICE_POLICY unset → all.  (§4)
R-A8  install.sh clobber (TA-C7): unconditional seed reverts operator tuning on upgrade.   (§4)
R-A9  Module-capture regression: a future import-time capture of these fields breaks hot-
      apply — enforced by the B2-0 capability probe + a static check.                      (B2 §11.4)
R-A10 rs-runtime.env key creep: a hand-added secret key leaks at 0o644 before B2's scan.
      Fixed key allowlist {ICE_POLICY, TURN_CRED_TTL}; unknown = deploy error.             (§3)
```

---

## 9. Acceptance criteria (assert against LIVE reality)
```
1. (TA-C1) Post-deploy on the LIVE unit: `systemctl show janus-camera-page -p Environment`
   contains NO ICE_POLICY, and -p EnvironmentFiles includes /etc/robot/rs-runtime.env.
   (NOT "the repo file changed".)
2. (TA-C4) On a COLOR node, post-deploy effective.ice_policy == "relay" (no unset window).
3. (TA-C2) Writing a malformed line into rs-runtime.env does NOT crash get_settings():
   it logs + falls back; readyz stays 200.
4. Capability probe flips ice_policy + turn_cred_ttl to CAPABLE (sentinel round-trips via
   os.environ + cache_clear → build_effective reflects it → restore).
5. (TA-C11) runtime_revision_store.py capability blocker text for NEW_SESSIONS_ONLY is
   updated to reflect the relocation (no longer "provided via systemd Environment=").
6. No other Settings field changed; no secret moved; no sandbox directive changed;
   /capabilities still apply_supported=false (Track A unblocks the field, not apply).
```

---

## 10. Test plan
```
- (TA-C9) regression guard goes through get_settings()+cache_clear (NOT bare Settings()):
  set os.environ, cache_clear, assert get_settings() reflects new ice_policy + turn_cred_ttl.
- without cache_clear → stays stale (necessity).
- (TA-C2) malformed TURN_CRED_TTL ("3600  # 1h", "x") → _int_env logs + returns default; no raise.
  malformed ICE_POLICY ("rely") → _str_env falls back to "all"; no silent relay-disable surprise
  (logged).
- (TA-C7) run the seed step twice with an operator edit between → the edit SURVIVES.
- (TA-C3) seed value is surface-correct: systemd seed=relay, compose/k8s seed=all.
- effective: relay unchanged end-to-end on the Pi after relocation.
```

---

## 11. Open questions
| OQ | Question | Default |
|---|---|---|
| A-OQ1 | Seed on the depth node too? | No — depth force-relays in code; a redundant ICE_POLICY source adds drift for no effect. Seed only color nodes. |
| A-OQ2 | k8s ConfigMap-API persistence — build with Track A or defer? | If k8s is in active use, **build it** (TA-C5 says live-only apply is unsafe there); if not, scope k8s apply OUT like helm. Don't ship live-only-with-silent-revert. |
| A-OQ3 | Also relocate other systemd `Environment=` knobs (ENCODER_DEFAULT_INSTANCE, …)? | No — Track A is the two apply-capable fields only. |
| A-OQ4 | Deploy/code order | settings.py fail-safe refactor first (backward-compatible — reads ICE_POLICY wherever it currently is); then the install.sh deploy-step + drop-in edit (seed-before-remove). Each independently safe. |

---

## 12. ADR summary (v2)
- **The relocation must deploy itself.** v1's biggest hole: no automation ships the drop-in, so a repo edit never reached the Pi (TA-C1). install.sh now installs it; acceptance asserts the live unit.
- **Fail-safe parsing is non-negotiable** (TA-C2): an operator-writable env file + an unguarded `int()` is an app-wide DoS. `_int_env`/`_str_env` land with the relocation.
- **"All surfaces" is honest only per-surface:** seed each surface's *current* value (systemd relay, cloud all); compose needs a writable mount + recreate; k8s live-apply without ConfigMap patch is a split-brain (loud or non-deferred); **helm is out**.
- The activation mechanism (`default_factory`+`cache_clear`) is **empirically proven** for both fields; `cache_clear()` stays mandatory.
- Track A makes the two fields **apply-capable**; it does not add apply. `/capabilities` stays `apply_supported=false` until the B2 engine + Track B land.

> Design-only, v2, corrected against an empirical adversarial review. No code, no IaC mutation, no deploy. First implementation step (A-OQ4) is the backward-compatible `settings.py` fail-safe refactor; the IaC deploy-step + drop-in edit follow, seed-before-remove.
