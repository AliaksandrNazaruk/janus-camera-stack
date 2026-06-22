# B2 Apply Engine — NEW_SESSIONS_ONLY slice (Design Spec v2)

**Status:** DESIGN ONLY — **v2, adversarial-review-corrected.** No code, no live mutation.
**Scope:** the first real `POST /apply`, ONLY `webrtc.ice_policy` + `webrtc.turn_credential_ttl_seconds`. No encoder/Janus/mountpoint/FDIR/Track-B. Stands on Track A + the B2-0 journal.
**Date:** 2026-06-18 · **Extends:** B2 v2, B2-0, Track A.

> A 2-reviewer adversarial pass (5 kill-zones) found **two empirically-reproduced
> criticals** (the apply lock self-deadlocks; the §5 hash fix fails open on an absent
> file) plus the fact that the **state machine it relies on doesn't exist yet**, a
> rollback that can leave a *rejected* `ICE_POLICY=all` durable on disk (a relay-bypass
> security regression), and a verify oracle that false-rejects on depth nodes. §0 is the
> corrections changelog. The protocol shape survives; the safety is in the corrections.

---

## 0. v2 corrections (adversarial review)

| # | v1 flaw | Reality (grounded/proven) | v2 resolution |
|---|---|---|---|
| **AE-C1** | §5 binds rs-runtime.env via hash-match | **PROVEN fail-open:** `file_hashes_before` gates the hash on `is_file()` (`runtime_revision_store.py:94`); absent file → `fhb={}`; `{}=={}` → base-match passes → applies against a non-existent base. Violates the acceptance requirement. | §5 — hash `RUNTIME_ENV_PATH` **unconditionally** for ice/ttl patches (`"sha256:__ABSENT__"` sentinel on absence) **+** structural reject in §3 if `file_hashes_before` lacks the rs-runtime.env key. |
| **AE-C2** | apply lock = "the same lock `write_env_atomic` uses, held whole-apply" | **PROVEN self-deadlock:** the apply holds `flock(LOCK_EX)` on `rs-runtime.env.lock`, then `write_env_atomic` opens a 2nd fd on the **same** path (`env_store.py:23`) and blocking-`flock(LOCK_EX)` (`:27`) → hangs forever. | §7 — a **separate** apply-lock path (`rs-runtime.env.apply.lock`); pass a distinct `lock_path` to `write_env_atomic` (the param exists) OR call a non-locking inner writer. |
| **AE-C3** | rollback "restores prior os.environ values" | A newly-introduced key (`TURN_CRED_TTL`) was **unset** before; restoring by assignment sets it to `"None"`/`""`, not delete → effective behavior diverges from true prior. | §8 — capture per-key sentinel `prior[K]=os.environ.get(K,_UNSET)`; rollback: `_UNSET → os.environ.pop(K)`, else set. |
| **AE-C4** | rollback failure is "low-severity" | rollback-file-write fail → **disk=new (rejected), process=old**; systemd `EnvironmentFile` loads disk on the next `Restart=always`/`WatchdogSec` restart → silently activates the **rejected** `ICE_POLICY=all` = **relay-bypass security regression**. | §8 — a rejected value must NEVER stay durable on disk: retry the file-restore; if it still fails, `recover_on_boot` (§9) detects disk≠last-good and refuses to trust it. Drop the "low-severity" claim for `ice_policy`. |
| **AE-C5** | "stale B2-0 revisions are safe" | No schema field on the record → can't tell a pre-§5 `fhb={}` from a legit one. | §5 — stamp `fhb_schema:2` + `binds:[…]` at validate; §3 rejects any revision lacking the stamp. |
| **AE-C6** | rs-runtime.env is `0644` | `write_env_atomic` **imposes** `os.chmod(0o644)` (`env_store.py:35`) → silently **weakens** an operator-hardened `0600`. | §4 — stat the existing mode, restore it after write; refuse to widen; warn if loosening. |
| **AE-C7** | "header comment may be re-emitted" | `read_env` strips `#` lines (`env_store.py:49`) → the allowlist-header (a security contract) is **lost** on first apply; the writer has no comment path. | §4 — MUST re-emit a fixed `# Allowlist: ICE_POLICY, TURN_CRED_TTL — managed by runtime-config apply` header (extend the writer / prepend). |
| **AE-C8** | "allowlist-assert: foreign key → reject" | Prose only — `write_env_atomic` writes the **whole** dict + re-chmods `0644`; a hand-added `TURN_SHARED_SECRET` would be **written through + world-exposed**. | §4 — concrete pre-write scan: after `read_env`, assert `set(cur) ⊆ {ICE_POLICY,TURN_CRED_TTL}` else **422 before any write**; run `SENSITIVE_KEYS` over the file's keys. |
| **AE-C9** | "reject if status≠validated" | No atomic compare-and-set; `get_revision` is a read-only copy → an `applied`/`rolled_back` revision can be **replayed** under a race/retry. | §3 — load→status-check→flip-to-`applying` **under the apply lock with a re-read**; reject anything not `validated` at that point. |
| **AE-C10** | verify = `build_effective().ice_policy == expected` | `build_effective` **forces** `ice_policy="relay"` on `camera_type=="depth_camera"` (`runtime_config_builder.py:84-85`) → a correct `ice_policy=all` apply on a depth node verifies as `relay≠all` → **false rollback**. | §10 — the verify oracle models the same conditional: compare `get_settings().ice_policy` (settings-level) for ice_policy; depth-node expected accounts for the forced relay. |
| **AE-C11** | only `/apply` takes the lock | `/validate` → `build_effective` → `get_settings` takes **no lock**; a validate during a mid-apply (post-`cache_clear`, pre-verify) journals a **torn** `effective_before`. | §7 — `/validate` snapshots under the apply lock (shared), or accepts torn-read with a documented bound; the §3.4 file hash is not the guard for the cached ice/ttl. |
| **AE-C12** | §11 state machine + recover_on_boot | **They don't exist** — the store writes only the literal `"status":"validated"`; no setter, no `recover_on_boot` anywhere. | §9 — specify `set_status()` (reuse `_atomic_write_json`, which already fsyncs) + a concrete `recover_on_boot` algorithm as **in-scope deliverables**. |
| **AE-C13** | rollback "restore file, env, cache_clear" (unordered) | A crash between env-restore and file-restore widens divergence; disk is the only crash-durable state. | §8 — pinned rollback order: env+cache first (in-proc), **file-restore last under the lock**; `recover_on_boot` keys off disk hash + `rolling_back` status. |
| **AE-C14** | "merge each changed key" (changed = ?) | A partially-no-op patch: `diff[]` drops the no-op field but `validated_patch` keeps it; ambiguous which drives the write. | §4 — read-merge-write iterates **`validated_patch`** allowlisted keys (not `diff[]`); written set always ⊆ allowlist; deterministic. |
| **AE-C15** | "any byte change → 409" | Correct (`file_hashes_before` hashes `read_bytes()`), but the **first apply canonicalizes** the file (writer emits bare `key=value`), so post-apply bytes differ from a seeded original. | §5 — note byte-instability across an apply is expected; the hash binds the *validated* base, recomputed each validate. (No fix; documentation.) |
| **AE-C16** | confirm binds diff_hash | `revision_id` embeds only 8 hex of the hash; a truncated/tampered record isn't cross-checked; stored `impact` could be stale if the policy map changed. | §3 — assert `revision.diff_hash[7:15]==revision_id` suffix; take `impact`/`valid` from the **re-validate** result (step 3), not the stored copy. |
| **AE-C17** | fsync "added" | `write_env_atomic` today has **no** flush/fsync before `shutil.move` (`env_store.py:31-36`) → a crash can leave a torn file already renamed in. | §6 — the writer must `flush()+fsync(tmp)` **before** move, then `fsync(dir)`; the apply fsyncs the `applying` journal **before** the write. Net-new, not a tweak. |
| **AE-C18** | (none) | Apply after a prior `rollback_failed`: §3.4 hashes **disk**, which may match a revision validated against the divergent disk → applies on an inconsistent process. | §7 — on entry, assert disk-vs-live coherence (`read_env[K]` vs `os.environ`/`get_settings`); divergent → dedicated 409 until reconciled. |
| **AE-C19** | whole-apply lock closes the 3.4→6 TOCTOU | A manual `vi` edit takes no flock; only a **re-hash right before write** catches it; §6 didn't re-check. | §6 — re-hash `rs-runtime.env` inside the lock immediately before the write; mismatch → abort. (Closes the window without "whole-apply" semantics.) |
| **AE-C20** | (none) | The verify must distinguish an **already-equal** apply (idempotent) from a real change for the journal/response. | §10 / AE-3 — if live==target for all keys, return `{status:"applied", changed:false}`, no write. |

---

## 1. Scope (unchanged)
Allowed: `POST /apply` of a journaled, valid, `{NEW_SESSIONS_ONLY}`-only revision; confirm-bound; full-file rs-runtime.env match; read-merge-write of `{ICE_POLICY,TURN_CRED_TTL}`; os.environ + cache_clear; verify; journal status. Forbidden: any other impact/field/file, FDIR, restart, reboot, rollback beyond rs-runtime.env.

---

## 2. Grounded primitives (with the gotchas the review exposed)

| Primitive | Location | Gotcha (must handle) |
|---|---|---|
| `write_env_atomic(data, env_path, lock_path)` | `env_store.py:13` | lock = `env_path+".lock"` → **self-deadlock if reused** (AE-C2); `chmod 0644` **weakens** (AE-C6); **no fsync** (AE-C17); writes whole dict, **no allowlist** (AE-C8) |
| `read_env` | `env_store.py:39` | **strips comments** (AE-C7) |
| `file_hashes_before` | `runtime_revision_store.py:84` | gates on `is_file()` → **{} on absence** (AE-C1) |
| `build_effective` | `runtime_config_builder.py:84-85` | **forces relay on depth_camera** (AE-C10) |
| revision store | `runtime_revision_store.py` | **no status setter / recover_on_boot** (AE-C12); `revision_id` = 8-hex of hash (AE-C16) |
| `_int_env`/`_str_env` (Track A) | `settings.py` | unset≠empty matters for rollback (AE-C3) |

---

## 3. Apply protocol (corrected)
```
POST /apply {revision_id, confirm}:
  acquire APPLY LOCK (separate path rs-runtime.env.apply.lock — AE-C2)
  re-read revision record from disk UNDER the lock (AE-C9):
    1. reject unless status==validated, fhb_schema==2 (AE-C5), impact (from re-validate) =={NEW_SESSIONS_ONLY},
       fields ⊆ {ice_policy, ttl}, and file_hashes_before CONTAINS rs-runtime.env key (AE-C1)
    2. confirm == "apply-<revision.diff_hash>" AND diff_hash[7:15]==revision_id suffix  else 400 (AE-C16)
    3. re-run validate(validated_patch) == {valid:true}; take impact from THIS result  else 422 (AE-C16)
    4. coherence: read_env(rs-runtime.env)[K] agrees with os.environ/get_settings for the keys  else 409 (AE-C18)
    5. recompute file_hashes_before (unconditional rs-runtime.env hash) == revision.file_hashes_before  else 409 (AE-C1)
    6. idempotency: if live==target for all keys → {status:applied, changed:false}, no write (AE-C20)
    7. flip status→applying (fsync) ; capture rollback_material (file content + per-key os.environ sentinels)
    8. WRITE (§6) ; VERIFY (§10) ; pass→applied / fail→rollback (§8)
  release lock
```

---

## 4. Field map + read-merge-write (allowlist-enforced)
- Map: `ice_policy→ICE_POLICY`, `turn_credential_ttl_seconds→TURN_CRED_TTL`. Other field → 422.
- `cur = read_env(rs-runtime.env)`; **assert `set(cur) ⊆ {ICE_POLICY,TURN_CRED_TTL}` and no `SENSITIVE_KEYS` else 422 BEFORE any write** (AE-C8); merge the `validated_patch` allowlisted keys (AE-C14); **preserve the existing file mode** (stat→restore, AE-C6); **re-emit the allowlist header** (AE-C7); write via the inner (non-locking) writer with **fsync** (AE-C17).

---

## 5. Hash binding (the fix that must land first)
`file_hashes_before` **unconditionally** includes `RUNTIME_ENV_PATH` for any patch touching ICE_POLICY/TURN_CRED_TTL: present → `sha256(bytes)`; absent → `"sha256:__ABSENT__"` (a distinct, matchable sentinel — absence at validate vs presence at apply now **mismatches** → 409). The record stamps `fhb_schema:2` + `binds:["/etc/robot/rs-runtime.env"]`. **§3.1 structurally rejects** any NEW_SESSIONS_ONLY revision whose `file_hashes_before` lacks the rs-runtime.env key — closing the `{}=={}` fail-open (AE-C1) and the user's acceptance requirement, independent of hash equality.

---

## 6. Write ordering + durability
```
journal status=applying (fsync via _atomic_write_json)        # AE-C12 setter
re-hash rs-runtime.env under the lock == expected else abort   # AE-C19 (close 3.4→6 TOCTOU)
write merged file: tmp → flush+fsync(tmp) → os.replace → fsync(dir)   # AE-C17, mode-preserved, header re-emitted
  ── on write failure → abort BEFORE os.environ; status back to validated; 500   # file-first (risk 6)
os.environ[K]=v ; cache_clear()
verify (§10)
```

---

## 7. Concurrency
- **Separate apply lock** `rs-runtime.env.apply.lock`; the inner writer gets a distinct `lock_path` (or is lock-free) (AE-C2). Whole apply (re-read→check→write→verify) under the apply lock; 2nd apply → 423.
- `/validate` snapshots under the apply lock (shared) so it can't journal a torn `effective_before` mid-apply (AE-C11).
- entry coherence check (AE-C18) refuses to apply on top of a `rollback_failed` divergence.

---

## 8. Rollback (corrected — bounded but NOT low-severity for ice_policy)
- capture `prior[K]=os.environ.get(K,_UNSET)` + full prior file content.
- order (AE-C13): restore os.environ (`_UNSET→pop`, else set — AE-C3) + cache_clear (in-proc), then **file-restore LAST under the lock**, integrity-guarded (on-disk == what we wrote, else don't clobber → `rollback_failed`).
- **rollback-file-fail (AE-C4):** retry; if still failing, the disk holds the rejected value → mark `rollback_failed` + `recover_on_boot` (§9) must detect disk≠last-good and refuse to trust rs-runtime.env on the next boot (a rejected `ICE_POLICY=all` surviving to a restart is a relay-bypass regression, not "low-severity").

---

## 9. State machine + recover_on_boot (net-new — AE-C12)
```
validated → applying → applied
                 └ verify-fail → rolling_back → rolled_back | rollback_failed
set_status(revision_id, status): reuse _atomic_write_json (fsync file+dir).
recover_on_boot(): for each revision in {applying, rolling_back}:
   h = sha256(rs-runtime.env bytes)
   applying:     h==target→applied ; h==prior→rolled_back ; else→rollback_failed
   rolling_back: h==prior→rolled_back ; else→rollback_failed
   on rollback_failed: surface on /status, do NOT advance last-good, require operator reconcile.
wired to a boot oneshot (or L4 startup, single-process).
```

---

## 10. Verify oracle (models the builder's conditional — AE-C10)
```
expected.ice_policy = "relay" if get_settings().camera_type=="depth_camera" else target.ice_policy
expected.turn_cred_ttl = clamp(target.ttl, 300, 3600)        # build_effective clamps (Track A bounds)
assert build_effective().webrtc.{...} == expected   (or compare get_settings()-level for ice_policy)
idempotent no-op (AE-C20) short-circuits before write.
```

---

## 11. Endpoint contract
`POST /api/v1/admin/runtime-config/apply {revision_id, confirm:"apply-<diff_hash>"}` → `200 {status:"applied", changed, verified}` · `400` confirm · `409` drift/coherence · `422` re-validate/mixed-impact/forbidden-field/foreign-key/missing-fhb-stamp · `423` lock · `500 {status:"rolled_back"|"rollback_failed"}`. Admin-gated + rate-limited + audit (revision_id).

---

## 12. Acceptance + test plan (incl. the proven-critical regressions)
```
PROVEN-CRITICAL regressions (must have explicit tests):
 R1 absent rs-runtime.env at validate+apply → NON-applyable (422 missing-fhb-stamp), NOT {}=={} pass (AE-C1).
 R2 the apply lock does NOT self-deadlock: an apply completes; concurrent apply → 423 (AE-C2).
 R3 rollback of a newly-introduced TURN_CRED_TTL → os.environ key is DELETED, get_settings()==3600 default (AE-C3).
 R4 rollback-file-fail → recover_on_boot refuses disk=rejected; ICE_POLICY=all never silently survives a restart (AE-C4).
 R5 verify on a (simulated) depth_camera: ice_policy=all apply does NOT false-rollback (AE-C10).
Plus: stale-schema revision rejected (AE-C5); chmod 0600 preserved (AE-C6); header re-emitted (AE-C7); foreign/secret
 key in file → 422 before write (AE-C8); status-replay of an applied revision rejected under lock (AE-C9); validate
 during apply not torn (AE-C11); byte-drift→409 (AE-C15); idempotent no-op→changed:false (AE-C20); fsync ordering
 (AE-C17); recover_on_boot reconciles applying/rolling_back (AE-C12); coherence refusal post-rollback_failed (AE-C18).
```

---

## 13. Open questions
| OQ | Question | Default |
|---|---|---|
| AE-1 | `/capabilities` once shipped | `supported_impacts:[NEW_SESSIONS_ONLY]`; RESTART_ENCODER stays Track-B-blocked |
| AE-2 | header content | fixed allowlist header, re-emitted every write (now a MUST, AE-C7) |
| AE-5 | does the inner writer get refactored out of env_store, or a distinct lock_path passed? | pass a distinct lock_path (smaller change); revisit if other callers need the lock-free body |
| AE-6 | recover_on_boot: boot oneshot vs L4 startup hook | L4 startup (single-process; same place the watchdog starts) |

---

## 14. ADR summary (v2)
- **Two criticals were reproduced before any code** — the lock self-deadlocks and the hash fix fails open. Both are now structural fixes (separate lock; unconditional hash + a stamp-based reject), not hash-equality hopes.
- **The state machine is net-new** (`set_status`/`recover_on_boot`) — v1 cited primitives that don't exist; v2 specs them.
- **Rollback is bounded but NOT benign for `ice_policy`** — a rejected value left on disk is a relay-bypass regression; recover_on_boot must refuse it.
- **The writer's gotchas are load-bearing** — self-lock, chmod-weaken, comment-strip, no-allowlist, no-fsync — each handled explicitly (separate lock, mode-preserve, header re-emit, allowlist-assert, fsync).
- **The verify oracle isn't pure** — it models `build_effective`'s depth-camera override or it false-rollbacks on exactly the nodes where relay matters.

> Design-only, v2, corrected against an empirical review. No code, no apply route, no live mutation. Implementation order when approved: §5 hash fix + stamp → the state-machine API (`set_status`/`recover_on_boot`) → the apply orchestration with the separate lock → rollback → tests R1–R5 first.
