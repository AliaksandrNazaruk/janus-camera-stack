# B2 Runtime Config Apply — Design Spec v2 (janus_camera_page)

**Status:** DESIGN ONLY — **v2, adversarial-review-corrected.** No implementation, no `/apply` engine, no admin UI, no live mutation, no service changes.
**Scope:** the *write* extension of the B1 read-only control plane — apply-plan, revision store, transaction/rollback boundary, post-apply verification, confirmation binding, the `Settings` activation model, and FDIR coordination. Organized by **`ApplyImpact` activation class**, not by endpoint.
**Date:** 2026-06-18 · **Target modules (future):** `app/services/runtime_config_apply.py`, `app/services/runtime_revision_store.py`, `app/routes/runtime_config.py` (additive).
**Extends:** `docs/design/B1_RUNTIME_CONFIG.md`.

> v1 was produced from a 7-area grounding survey. **v2 incorporates a 3-reviewer
> adversarial review (10 attack angles) that empirically falsified v1's central
> activation hinge and found a critical indirect-reboot path.** §0 is the
> corrections changelog; every fix below is grounded against real code + a run.

---

## 0. v2 corrections (adversarial review)

The review **falsified the v1 NEW_SESSIONS_ONLY model** and surfaced a critical indirect-reboot path. Both in-scope apply classes turned out to have **blocking prerequisites**; the only immediately-buildable step is the read-only journal (§4.2). Every correction:

| # | v1 claim | Reality (grounded) | v2 resolution |
|---|---|---|---|
| C1 | `get_settings.cache_clear()` makes an env write visible | `Settings` is `@dataclass(frozen=True)` (`settings.py:80`); `ice_policy`/`turn_cred_ttl` defaults are `os.getenv(...)` **evaluated once at import** and stored as frozen literals. Proven by run: after `os.environ['ICE_POLICY']='relay'` **and** `cache_clear()`, `get_settings().ice_policy` is **still `all`**; even a fresh `Settings()` is `all`. | §11 reworked: applying a Settings field requires **(a)** refactor to a call-time read (`default_factory`/read inside `get_settings()`), **(b)** `os.environ[KEY]=value` mutation, **(c)** a writable, systemd-loaded, non-shadowed file — plus a round-trip capability probe. |
| C2 | `ice_policy` write target `/etc/robot/camera.env` | `ICE_POLICY=relay` is injected by systemd `Environment=` in the unit drop-in (shadows `EnvironmentFile=`); `/etc/robot/camera.env` does not exist; the only loaded env file (`camera-secrets.env`) is bind-mounted **read-only**. | `ice_policy`/`turn_cred_ttl` are **NOT apply-capable on the current deployment**. §4 marker corrected; relocation is a prerequisite. |
| C3 | "No reboot — ever" (G7) is satisfied because no step calls reboot | A color encoder restart makes `video_age_ms`+snapshot stale → the **autonomous FDIR watchdog** (8 s loop, 10 s threshold, grace = startup-only) escalates the ladder → `restart_pipeline → restart_janus → reboot_node` → **physical reboot if `CAM_WATCHDOG_REBOOT_ENABLED=1`**. | New §12 (FDIR quiesce) is **mandatory** before any RESTART_ENCODER. G7 reworded to "no step, and no step's *side effects*, may reach reboot." |
| C4 | base-state match = `build_effective() == effective_before` | `build_effective()` color view reads only `WIDTH/HEIGHT/FPS/BITRATE_KBPS/GOP`; the real file also holds `PRESET/TUNE/SNAPSHOT_FPS/PORT/ROTATION`. Match passes while the file differs → validate-A/apply-B. | §7 base-match now asserts **full `file_hashes_before`** of every `files_to_touch`, not effective-equality. |
| C5 | `write_env_atomic` writes the changed key | It writes **only** the dict given and replaces the whole file — no merge (`env_store.py:31-34`). Writing one key **deletes** the rest. | §6/§8: all env writes are **read-merge-write**; `rollback_material` = full prior file content. |
| C6 | reuse `_flock_state` "→ os.replace → fsync" | `_flock_state` has **no fsync** (`mountpoint_allocator.py:160-162`); `write_env_atomic` has none either. Crash between write and journal → mutate-twice. | §5/§7: **explicit fsync** (file + dir) and ordered journaling: intent → write → commit. |
| C7 | status machine is enough | No boot-time entry; a crash (OOM, `MemoryMax=512M`, `Restart=on-failure`) between steps strands a revision in `applying` with the write live and no owner. | §6.4 adds a **`recover_on_boot()`** rung. |
| C8 | verify = `is_running && video_age_ms ≤ stale` | Color mountpoint **1305 is static** and survives the encoder restart; Janus `age_ms` keeps counting from the **old** stream → reads fresh (<10 s) for the whole window; `is_running` is true the instant systemd is `active`, before first frame → **false PASS** (commit before any new frame). | §9 verify uses a **restart epoch `t0`** + proof of a *new* post-`t0` frame, with an expected dip-then-recover. |
| C9 | `canonical_json` (named, used in the hash) | Never defined → key order/unicode/float/None gaps let two intents collide or a benign re-serialize mismatch the token. | §10 pins RFC 8785 JCS over the **pydantic-normalized** form. |
| C10 | apply flock gives serialization | The legacy `POST /color/config` + `admin_config` writers take their **own** file locks, not the apply lock; `build_effective()` (slow subprocess probes) is called twice, widening the window. | §7: apply lock is the **same writer lock** the tuning/config writers take; `effective_before` snapshotted **once** under it. |
| C11 | journal is secret-free (effective_before is) | True for `effective_before` (verified secret-free, `runtime_config_builder.py:39-46`), but `rollback_material`/`file_hashes_before` over a future secrets-adjacent file could journal secrets; `GET /revisions` would re-expose. | §5.4 structural gate: write targets must be **allowlisted free of `SENSITIVE_KEYS`**; `GET /revisions` redacts `rollback_material`. |
| C12 | plan = transform of `diff[]` | `validate()` skips `from == to` (`runtime_config_validator.py:172`), so an equal-valued patch field is absent from `diff[]` but present in `validated_patch`/`diff_hash`. | §6.1 plan is derived from **`validated_patch`** (intent); `diff[]` is impact classification only. |

---

## 1. Purpose & Non-Goals

### 1.1 Purpose
B1 made the operator-tunable L4 surface typed, observable, and dry-run validatable. B2 designs the **safe write path**: how a *previously validated* patch becomes live, is verified, and is rolled back on failure — without silent mutation, without rebooting, and **without the autonomous watchdog mistaking a planned change for a fault** (§12).

### 1.2 First principles (non-negotiable)
```
B2 = apply DESIGN, not apply code.
Default remains dry-run (B1 /validate unchanged).
Every apply MUST reference a previously validated diff hash, re-checked server-side.
No silent mutation — every write is journaled with before/after + fsync.
No reboot — and no step whose SIDE EFFECTS can reach the reboot rung (§12).
A field is applyable only if a capability probe proves it round-trips (§11.3).
```

### 1.3 Explicit Non-Goals (B2 does NOT)
```
No reboot apply (direct or watchdog-indirect — §12).
No Janus restart apply (RESTART_JANUS deferred / maintenance-window class).
No mountpoint recreate apply (RECREATE_MOUNTPOINT deferred to B3/B4).
No admin UI · No arbitrary env editor · No secrets mutation.
No firewall / bind / deployment-field mutation (R10 stays REJECTED).
No depth/IR runtime tuning (R6b stays REJECTED).
No apply without a prior validate hash + full-file base match.
No best-effort partial apply — all-or-nothing with rollback.
No apply of a field that fails the §11.3 capability probe.
```

---

## 2. The B1 contract being extended
Unchanged from B1: `RuntimeConfig` + sub-models, `build_effective()` (reused as snapshot source + verification oracle), `validate()` (mandatory pre-apply gate + diff-hash source), `ApplyImpact` (apply fan-out key), `DiffEntry`. **No new `RuntimeConfig` fields.** B2 adds out-of-band models (revision, apply-plan, apply-result) that wrap a validated patch.

---

## 3. Existing primitives discovered in grounding

| Primitive | Location | Verdict (post-review) |
|---|---|---|
| `write_env_atomic()` — flock `LOCK_EX` + tmp + `shutil.move` | `env_store.py:13` | ✅ reuse, but **full-file replace, no merge, no fsync** → §6 read-merge-write + §5 fsync |
| `_flock_state()` ctxmgr — flock + tmp + `os.replace` | `mountpoint_allocator.py:129` | ✅ pattern, but **no fsync** (C6) |
| `_save_env()` — tmp + `os.rename`, `0o600` | `secret_store.py:114` | reference only (never touch the secrets file) |
| `write_reboot_count()` — flock + **fsync** + TOCTOU-safe | `recovery_persistence.py:63` | ✅ the durability model to copy (the one that *does* fsync) |
| `POST /api/v1/admin/config/apply` — rotate→render→restart | `admin_config.py:239` | ⚠️ shape only; it is the secrets+jcfg path, **not** the B1 surface; **takes its own lock** (C10) |
| confirm idiom `confirm == "reveal-{key}"` | `admin_config.py:175` | ✅ extend to `apply-<diff_hash>` |
| `get_settings()` `@lru_cache`; **frozen-dataclass literal defaults** | `settings.py:80,127,133,183` | ⚠️ **the C1 trap** — cache_clear cannot refresh; see §11 |
| autonomous watchdog + recovery ladder (8 s loop, reboot rung) | `watchdogs.py`, `recovery_executor.py`, `recovery_policy.py` | ⚠️ **the C3 trap** — must be quiesced (§12) |
| `is_running()` via `encoder-admin status`; `video_age_ms` via Janus `age_ms`; static color mp 1305 | `sensor_lifecycle.py:66`, `janus.py:247` | ⚠️ **false-pass trap** (C8) — §9 |
| `healthz`/`/health/stream` (stale = `watchdog_stale_ms=10s`) | `system.py:92,123` | steady-state; transient-blind during restart |

---

## 4. ApplyImpact → activation mapping (corrected) + buildable scope

| Impact | Write target | Activation | Verify | Rollback material | B2 status |
|---|---|---|---|---|---|
| `HOT` | in-process | refresh view | re-read effective | prior value | design-only (no HOT field today) |
| `NEW_SESSIONS_ONLY` | a **writable, non-shadowed, secret-free** EnvironmentFile (NOT today's reality for `ice_policy`/`ttl` — C2) | refactor field to call-time read + `os.environ[KEY]=v` + `cache_clear()` (§11) | re-read effective == expected (capability-probed, §11.3) | full prior file content | **PREREQ-GATED** (relocate + refactor first) |
| `RESTART_ENCODER` (color) | `rs-color.tuning.env` via **read-merge-write** | **FDIR-quiesce (§12)** → restart `rs-stream@color` → un-quiesce | epoch-`t0` settle-loop (§9) | full prior `rs-color.tuning.env` snapshot | **PREREQ-GATED** (needs §12 quiesce) |
| `RECREATE_MOUNTPOINT` | allocations + Janus mount | recreate via Janus admin | mount present + re-attached | prior allocation + mount | **DEFERRED** (B3/B4) |
| `RESTART_JANUS` | Janus config + restart | maintenance window | full health-stream recovery | prior Janus config | **DEFERRED** |
| `DEPLOYMENT_ONLY` | — | none at runtime | — | — | **REJECTED from apply** |
| `REJECTED` | — | never | — | — | **NEVER applied** |

### 4.1 Corrected scope reality
The review showed **both** in-scope classes are prerequisite-gated:
- `NEW_SESSIONS_ONLY` cannot apply `ice_policy`/`turn_cred_ttl` until they are (i) moved out of the systemd `Environment=` drop-in into a writable, loaded, secret-free EnvironmentFile, and (ii) refactored from frozen-literal defaults to call-time reads. Until then no field qualifies (§11.3 probe fails).
- color `RESTART_ENCODER` cannot apply until the FDIR quiesce mechanism (§12) exists, the write is read-merge-write (C5), and verify is epoch-based (C8).

### 4.2 The only immediately-buildable step — B2-0 (journal-only, read-only)
```
B2-0 (no mutation, lowest risk — build first):
  - /validate additionally persists a "validated" revision (revision_id, diff_hash, plan)
  - GET /api/v1/admin/runtime-config/revisions/{id}   (read-only, redacted)
  - the §11.3 capability probe implemented as a READ-ONLY preflight that REPORTS
    apply-capability per field (writes a sentinel ONLY to a scratch path, never a live file)
Everything that mutates live state stays gated behind C1/C2 (Settings) and §12 (FDIR).
```

> **Scope marker (corrected):** B2 *implementation* begins with **B2-0 journal-only**. The two apply classes are **design-complete but prerequisite-blocked**; no apply/restart code is written until those prerequisites land and are separately approved.

---

## 5. Revision store model

### 5.1 Record shape
```jsonc
{
  "revision_id": "rev-<ts>-<shorthash>",
  "created_at": "<ISO8601, stamped by caller>",
  "actor": { "admin": true, "source_ip": "...", "request_id": "..." },
  "diff_hash": "sha256:…",            // = JCS(patch_normalized) ‖ file_hashes_before — §10
  "validated_patch": { … },           // the operator's stated intent (plan source — C12)
  "effective_before": { … },          // secret-free build_effective() snapshot
  "diff": [ DiffEntry… ],             // impact classification only (NOT the plan source)
  "impact": ["…"],
  "plan": [ ApplyStep… ],             // derived from validated_patch (§6.1)
  "files_to_touch": ["…"],
  "file_hashes_before": { "<path>": "sha256:<full-file>" },   // base-match + integrity (C4)
  "file_hashes_after_forward": { "<path>": "sha256:…" },      // captured INSIDE the write flock (C8/integrity)
  "rollback_material": { "<path>": "<full prior file content, redacted on read>" },  // C5/C11
  "status": "validated"               // §6.2 + §6.4
}
```

### 5.2 Storage mechanism (durable — C6)
- Path **`/var/lib/camera-fdir/runtime_revisions/`** (OQ-1 decided), one file per revision + an append-only index journal.
- Writes follow the **`recovery_persistence` durability model**, not bare `_flock_state`: flock `LOCK_EX` → write tmp → `os.replace` → **`fsync(file)` + `fsync(dir)`**.
- **Ordered journaling:** write `status:"applying"` intent record (fsync) **before** any live write; flip to `committed`/`rolled_back` (fsync) **after**. This is what makes §6.4 crash recovery possible.

### 5.3 Retention (OQ-2 decided)
Keep the **last 50** revisions + a **permanent `last-good` pointer** (the last `committed` revision); prune older, never prune `last-good`.

### 5.4 Secret-safety (structural — C11)
- A `settings_env` write target is admissible **only if** a plan-time scan proves the file contains **no key in `secret_store.SENSITIVE_KEYS`** (allowlist).
- `rollback_material` (full file content) and `file_hashes_before` are **redacted** by `GET /revisions/{id}` — the same secret-exclusion discipline as `/effective`, applied to the journal payload, not just `effective_*`.

---

## 6. Apply-plan model

### 6.1 ApplyStep (plan derived from `validated_patch` — C12)
```jsonc
{
  "step_id": 1, "path": "stream_profiles.<serial>:color.bitrate_kbps",
  "impact": "RESTART_ENCODER", "to": 1200,
  "write":  { "kind": "color_tuning_env", "file": "/etc/robot/rs-color.tuning.env",
              "mode": "read-merge-write", "keys": { "BITRATE_KBPS": 1200 } },   // C5
  "activate": { "kind": "encoder_restart", "instance": "color", "quiesce_fdir": true },  // C3/§12
  "verify": { "kind": "encoder_settle", "epoch_relative": true },               // C8
  "rollback": { "restore_file_content": "<full prior>", "then": "encoder_restart" }
}
```
`write.kind` ∈ `{settings_env, color_tuning_env}` (allocation deferred); all env writes are read-merge-write. `activate.kind` ∈ `{none, settings_cache_clear, encoder_restart}`; `encoder_restart` **requires** `quiesce_fdir:true`.

### 6.2 Status state machine
```
validated → planned → applying → verifying → committed
                          │           │
                          └─ fail ────┴─→ rolling_back → rolled_back
                                                     └─→ rollback_failed (→ SAFE + CRITICAL)
```
`committed` atomically advances `last-good`. `rollback_failed` is the only terminal-bad state → SAFE mode + CRITICAL event; never leaves silent drift.

### 6.3 Ordering (corrected — C-review POINT 5)
v1 said "least-disruptive first." The review showed that maximizes the *irreversible-side-effect* window (NEW_SESSIONS_ONLY mints live sessions, then a later encoder failure can't un-mint them). **v2 rule:** order the **highest-rollback-risk step first** (encoder RESTART_ENCODER before NEW_SESSIONS_ONLY), so a late failure rolls back the *reversible* surface and never strands emitted sessions on a transient value. Single-impact revisions are unaffected.

### 6.4 Crash recovery (`recover_on_boot()` — C7)
On L4 startup, scan the revision store for any revision in `applying`/`verifying`/`rolling_back`. For each: re-derive on-disk file hashes vs `file_hashes_before`/`*_after_forward` and drive it to a terminal state — complete-forward (if write landed, verify now), roll-back-reverse (if not), or `rollback_failed`→SAFE if ambiguous. This mirrors the `sensor_lifecycle` "intent-before-action" survivability, made explicit for apply.

---

## 7. Transaction / saga boundary

**Grounded constraint:** per-file atomicity ≠ multi-file transaction; legacy writers don't take the apply lock (C10).

### 7.1 Boundaries
- **One writer lock for `rs-color.tuning.env`** — the apply path and the legacy `POST /color/config` / tuning writers acquire the **same** flock (not per-endpoint locks). One apply at a time process-wide; concurrent apply → `423`.
- **Per-step atomicity** from the atomic writers; **inter-step consistency** from the saga (capture `rollback_material` *before* each write; on failure run §8 reverse over completed steps).
- **`effective_before` + `file_hashes_before` snapshotted once under the lock** (no double `build_effective()` probe — C10).
- **Idempotency** keyed on `diff_hash`; a retry returns the committed result **only if** the live `file_hashes` still match `file_hashes_after_forward`, else it is a conflict, not a no-op (C5/C8).

### 7.2 Pre-flight invariants (all hold or apply is refused before any write)
```
1. server-side validate(validated_patch) == { valid:true }     (never trust client)
2. confirm == "apply-<diff_hash>"                               (§10)
3. full file_hashes(files_to_touch) == file_hashes_before       (C4 — not effective-equality)
4. every step's field passed the §11.3 capability probe
5. impact ⊆ { NEW_SESSIONS_ONLY (probe-passing), RESTART_ENCODER(color) }
6. no DEPLOYMENT_ONLY / REJECTED step
7. writer lock acquired (shared with legacy writers)
8. no step's side effects can reach reboot — FDIR quiesce armed for restart steps (§12)
```

---

## 8. Rollback model

### 8.1 Per-write-kind rollback (full-content — C5)
| write.kind | Capture before (under the write flock) | Restore |
|---|---|---|
| `settings_env` | **full prior file content** (+ full-file hash) | rewrite full prior content (read-merge-write) → `os.environ` restore → `cache_clear()` → assert effective == before |
| `color_tuning_env` | **full prior `rs-color.tuning.env`** (+ hash) | restore full content → **quiesce-wrapped** `rs-stream@color` restart → epoch-settle-verify |

### 8.2 Integrity guard (window closed — C-review POINT 4)
`file_hashes_before` **and** `file_hashes_after_forward` are captured **inside the same flock** as the forward write (not a later journal step). Rollback compares on-disk to `file_hashes_after_forward`: a mismatch means a foreign edit landed after our write → **do not overwrite**, escalate `rollback_failed`→SAFE. Any out-of-band edit that does not take the shared writer lock is out of contract.

### 8.3 Reversibility caveats
- `settings_cache_clear` rollback restores the *configuration*; already-issued WebRTC sessions persist by definition (acceptable for NEW_SESSIONS_ONLY). Ordering (§6.3) ensures these are never stranded by a *different* step's failure.
- A failed *rollback* restart → `rollback_failed`→SAFE.

---

## 9. Post-apply verification (epoch-based — C8)

Per-impact, settle-aware, and **anchored to a restart epoch** so the static color mountpoint cannot false-pass:

| Impact | Procedure |
|---|---|
| `NEW_SESSIONS_ONLY` | after `os.environ` set + `cache_clear()`: `build_effective()`, assert field == `to`. Immediate. (Only runs once §11.3 proves the field round-trips.) |
| `RESTART_ENCODER` (color) | capture monotonic `t0` **before** issuing the restart. Settle-loop (`T=20s`, `p=1s`, `k=3` — OQ-7): require evidence of a **new frame after `t0`** — either Janus packet/byte counters **increase** past their `t0` baseline, or `age_ms < p` (a packet landed within the last poll) — sustained `k` consecutive probes. Do **not** accept a bare `age_ms ≤ watchdog_stale_ms` (the old stream reads fresh for ~10 s — C8). `is_running` true is necessary but not sufficient. |
| `HOT` | re-read effective, assert. |

Verify failure → §8 rollback. Pass → `verifying → committed`, advance `last-good`. `T` is 20 s (not 15) to absorb RealSense/ffmpeg/Janus cold-start tails — a false rollback is worse than 5 s of waiting (OQ-7).

---

## 10. Explicit admin confirmation (TOCTOU-closed)

### 10.1 Diff hash (canonicalization pinned — C9)
```
diff_hash = sha256( JCS(patch_normalized) ‖ "\n" ‖ canonical(file_hashes_before) )
```
- `JCS` = RFC 8785 JSON Canonicalization Scheme (UTF-8 NFC, lexicographically sorted keys, shortest-number form, no insignificant whitespace, duplicate keys rejected).
- `patch_normalized` = the patch **after pydantic coercion** (so `30` and `30.0` and client key-order all normalize identically), not raw client bytes.
- Binding to `file_hashes_before` (not the secret-free `effective_before`) means the token authorizes one change against the **exact on-disk bytes** it was validated against — closing the C4 blind-spot.

### 10.2 Two-call protocol
```
POST /validate → { valid, diff, impact, revision_id, diff_hash }   (persists a "validated" revision)
POST /apply    { revision_id, confirm:"apply-<diff_hash>" }
   200 { status:"committed", verified:true } · 400 confirm mismatch ·
   409 file_hashes drift · 422 re-validate failed · 423 writer lock held ·
   500 { status:"rolled_back" | "rollback_failed" }
```
Confirm is a **bound phrase**, never free text; apply re-runs validate server-side; the client's plan is never trusted (the persisted plan is used).

---

## 11. Settings activation model (reworked — C1/C2)

### 11.1 The grounded truth
`get_settings()` is `@lru_cache` (`settings.py:183`) **and** `Settings` is `@dataclass(frozen=True)` whose fields use literal defaults `os.getenv(...)` evaluated **once at import** (`settings.py:127,133`). Proven by run:
```
os.environ['ICE_POLICY']='relay'; get_settings.cache_clear()
get_settings().ice_policy → 'all'      # cache_clear does NOT help
Settings().ice_policy      → 'all'      # a fresh instance does NOT re-read env
```
So a file write is inert **and** so is `cache_clear()` alone. Three things must all be true for a Settings field to be runtime-applyable:

### 11.2 Required changes (all three)
```
(a) READ-AT-CALL-TIME: refactor the field from a frozen literal default to a per-construction
    read — field(default_factory=lambda: os.getenv("ICE_POLICY","all")) OR move the read into
    the get_settings() body. (Factories re-run per instantiation; frozen literals do not.)
(b) PROCESS-ENV MUTATION: at apply, set os.environ[KEY]=value (a file write never updates the
    running process env) AND call get_settings.cache_clear().
(c) DURABLE, NON-SHADOWED FILE: persist to a file systemd actually loads on restart, NOT
    shadowed by a unit `Environment=` directive, NOT a read-only bind mount, NOT a secrets file.
```

### 11.3 Capability probe (pre-flight gate — replaces blind classification)
Before a field is treated as applyable, prove it round-trips on **this** deployment:
```
probe(field):
  resolve write target + env KEY
  if target is shadowed by Environment= / read-only / secrets-bearing → NOT-CAPABLE (reject)
  (read-only mode, scratch copy) write sentinel → os.environ[KEY]=sentinel → cache_clear()
  if build_effective() reflects sentinel → CAPABLE ; else NOT-CAPABLE
  restore
```
A field that fails the probe is rejected from apply with a clear "not runtime-applyable on this deployment (requires relocation/refactor)" error. On the current node, `ice_policy`/`turn_cred_ttl` **fail** the probe (C2) until relocated.

### 11.4 Module-capture guard (still required)
`cache_clear()`/factory-read only help consumers that call `get_settings()` at use time. A value captured into a module global at import (e.g. `_CAM_TYPE = get_settings().camera_type`, `janus.py:38`) is immune. **Rule:** no field is applyable unless a static check proves its live consumer reads `get_settings()` at request/use time — enforced by the §11.3 probe (which would not round-trip a module-captured consumer) **and** a test.

---

## 12. FDIR coordination — quiesce around planned restarts (NEW — C3)

**The indirect-reboot path (grounded):** a color `RESTART_ENCODER` restarts `rs-stream@color` → Janus `video_age_ms` and `/run/realsense/color-snapshot.jpg` go stale → the **autonomous watchdog** (`watchdog_interval=8s`, `watchdog_stale_ms=10s`, grace = startup-only) escalates the recovery ladder → for a color node: `retry_handle → restart_pipeline → restart_janus → reboot_node` → `sudo systemctl reboot` **if `CAM_WATCHDOG_REBOOT_ENABLED=1`** (a supported deployment). Even with reboot disabled, the ladder's `restart_pipeline`/`restart_janus` fire **mid-apply**, corrupting the very stream the verify depends on.

**Mandatory design (before any RESTART_ENCODER impl):**
```
- Add a process-wide "planned maintenance" guard (apply_in_progress, or extend
  _in_grace_period semantics) that the watchdog loop AND the snapshot-watchdog loop
  check and treat exactly like the startup grace period.
- ARM it before the encoder restart; keep it armed for the settle window T + margin;
  CLEAR it on commit OR rollback (and on recover_on_boot of a stuck revision).
- While armed, watchdog escalation for THIS sensor's signals is suppressed (the
  ladder is not advanced; steady-state health still observed for the apply's own verify).
- Regression test: a B2 color restart causes ZERO ladder.escalate() calls.
```
This makes G7 true in effect, not just literally. Until it exists, color `RESTART_ENCODER` stays deferred alongside the higher-risk classes.

---

## 13. Safety gates (consolidated)
```
G1  Apply requires server-side validate == {valid:true}.            (never trust client)
G2  Apply requires confirm == "apply-<diff_hash>" (JCS-pinned, §10).
G3  Apply requires full file_hashes(files_to_touch) == file_hashes_before (409 on drift, §7.2.3).
G4  Every applied field passed the §11.3 capability probe.
G5  Only NEW_SESSIONS_ONLY(probe-passing) + color RESTART_ENCODER(quiesced) are applyable in B2.
G6  DEPLOYMENT_ONLY → refused; REJECTED → never.
G7  No step, AND NO STEP'S SIDE EFFECTS, may reach reboot — FDIR quiesced for restarts (§12).
G8  One shared writer lock; concurrent apply → 423; legacy writers share it (C10).
G9  Rollback material = full prior file content; integrity hashes captured inside the write flock.
G10 Durable journaling: intent→write→commit, each fsync'd; recover_on_boot drives stuck revisions.
G11 rollback_failed → SAFE + CRITICAL; never silent drift.
G12 Revision store secret-free: allowlisted targets + redacted GET /revisions.
G13 RESTART_JANUS / RECREATE_MOUNTPOINT refused in B2.
G14 Every apply/rollback audit-logged with revision_id.
```

---

## 14. Endpoint contract (design only)
```
POST /api/v1/admin/runtime-config/validate
     (B1 behavior + persists a "validated" revision, returns revision_id + diff_hash. Back-compatible.)
GET  /api/v1/admin/runtime-config/revisions/{revision_id}
     (read-only, secret-redacted)            ← B2-0, buildable now
POST /api/v1/admin/runtime-config/apply      { revision_id, confirm:"apply-<diff_hash>" }
     (gated behind §11.3 capability + §12 quiesce; not buildable until prereqs land)
POST /api/v1/admin/runtime-config/rollback   { revision_id, confirm:"rollback-<revision_id>" }
```
Additive to the B1 router. **B2-0 (validate-journaling + GET /revisions + the read-only capability report) is the only part with no live mutation and is the first implementable.**

---

## 15. Test plan
**B2-0 (journal-only)**: validate persists a redacted revision; GET /revisions never exposes secrets/rollback_material; capability probe reports `ice_policy` NOT-CAPABLE on this deployment (C2 regression); revision store write is fsync-durable + survives concurrent writers.
**Settings activation (when unblocked)**: a refactored field round-trips (`default_factory` + `os.environ` + cache_clear → fresh `Settings()` reflects mutated env); the **frozen-literal** form does NOT (C1 regression guard); module-captured consumer fails the probe (C-guard).
**Color RESTART_ENCODER (when unblocked)**: read-merge-write preserves `PRESET/ROTATION/SNAPSHOT_*` (C5); verify requires a post-`t0` new frame and does NOT pass on residual `age_ms` (C8 false-pass regression); **a color restart causes zero `ladder.escalate()` calls** (C3 regression).
**Apply gating**: no prior validate→422; wrong confirm→400; file-hash drift→409 (C4); DEPLOYMENT_ONLY/secret/R10→refused; concurrent apply→423; legacy `/color/config` during apply blocked by the shared lock (C10).
**Rollback / crash**: forced verify-fail restores full prior file + cache_clear; out-of-band edit (with the shared lock) → rollback_failed→SAFE, no clobber (C-POINT4); crash between steps → recover_on_boot drives to terminal (C7); idempotent re-apply returns prior result only if live hashes still match (C5/C8).
**Safety**: no path invokes reboot directly or via the ladder; audit carries revision_id.

---

## 16. Open Questions / Deferred work
| OQ | Question | Decision |
|---|---|---|
| OQ-1 | Revision store path | **`/var/lib/camera-fdir/runtime_revisions/`** (decided) |
| OQ-2 | Retention | **last 50 + permanent last-good** (decided) |
| OQ-7 | Settle `T`/`p`/`k` | **T=20 s, p=1 s, k=3** (decided; raised from 15 s) |
| OQ-10 | Runtime-toggle `reboot_allowed`? | **NO** — deploy-time only (decided) |
| §4 | In-scope boundary | **B2-0 journal-only now**; both apply classes prereq-gated (decided) |
| OQ-11 (new) | Relocate `ice_policy`/`turn_cred_ttl` to a writable EnvironmentFile + refactor to call-time read — infra change owner? | prerequisite for NEW_SESSIONS_ONLY; needs a deployment/IaC change (out of L4 scope) |
| OQ-12 (new) | FDIR quiesce: new `apply_in_progress` flag vs extending `_in_grace_period`? | lean: extend grace mechanism (smaller surface); confirm in §12 impl review |
| OQ-13 (new) | Janus packet/byte counter availability for epoch verify (vs `age_ms<p`) | verify `janus_summary` exposes a monotonic counter; else fall back to `age_ms<p` + dip-observed |
| OQ-4 | Cross-field ordering beyond "highest-rollback-risk first" | fixed total order per impact; revisit on real multi-field cases |
| OQ-5 | Multi-worker apply lock | single-process today (`--workers 1`, verified); document assumption |

---

## 17. ADR summary (v2)
- **The v1 activation hinge was empirically false** (C1) and the canonical in-scope field isn't file-applyable on this deployment (C2): `cache_clear()` is necessary-but-insufficient; runtime Settings apply needs call-time reads + `os.environ` mutation + a writable non-shadowed file + a **capability probe** that rejects fields that can't round-trip.
- **B2 must coordinate with FDIR** (C3): a planned encoder restart is indistinguishable from a fault to the autonomous watchdog and can reach the reboot rung. Quiesce is mandatory; "no reboot" now covers side effects, not just direct calls.
- **Integrity is full-file, not effective-view** (C4/C5): base-match and rollback operate on whole-file hashes/content with read-merge-write; the effective view is too narrow to bind a safe apply.
- **Durability + crash recovery are explicit** (C6/C7): fsync'd ordered journaling + `recover_on_boot`.
- **Verify is epoch-anchored** (C8): the static mountpoint makes naive freshness a false-pass.
- **Confirm binds JCS(patch)‖file_hashes** (C9) and the **journal is structurally secret-free** (C11).
- **Buildable scope shrank to B2-0 journal-only**; both apply classes are design-complete but prerequisite-blocked.

> v2 is design-only and corrected against an empirical adversarial review. No Python, no `/apply` route, no service mutation written. Implementation begins with **B2-0 (journal-only, read-only)**; the apply classes wait on their §11/§12 prerequisites and a further review.
