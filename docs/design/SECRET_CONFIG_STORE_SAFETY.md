# SECRET_CONFIG_STORE_SAFETY — Cycle 1 / Phase 1 recon + plan (GATED, no code yet)

New hardening cycle (consistency layer, not cosmetics). Closes the audit's **High/near-Critical**
secret/config-store finding: a corrupt store read as empty → silent regeneration / lost update →
control-plane ↔ node/runtime mismatch. Reuse the existing fail-closed pattern; no store-layer rewrite.
No code until GO.

## Recon — store-by-store status (verified 2026-06-21)
The codebase ALREADY has the gold-standard fail-closed pattern (do NOT reinvent): `stream_binding_store/
state_file.py` (H-02) = `StoreCorruptionError` + `_quarantine_corrupt_state` (timestamped `.corrupt.<ts>`
forensic copy) + flock + atomic write + fsync; `operation_journal.py` (H3) = quarantine + `JournalCorrupt`.

| store | corrupt read | flock | atomic+fsync(file) | dir-fsync | quarantine | verdict |
|---|---|---|---|---|---|---|
| `stream_binding_store/state_file.py` | **raise+quarantine** | ✅ | ✅ | ❌ | ✅ | DONE (H-02) — leave |
| `operation_journal.py` | **raise+quarantine** | n/a | ✅ | ❌ | ✅ | DONE (H3) — leave |
| **`secret_store.py`** (camera-secrets.env) | lenient line-skip → partial | ❌ | ❌ (os.rename, no fsync) | ❌ | ❌ | **FAIL-OPEN — Tier 1** |
| **`stream_binding_store/secrets.py`** (node_secrets.json) | **`return {}`** | ❌ | ✅ | ❌ | ❌ | **FAIL-OPEN → token regen — Tier 1** |
| `runtime_config_apply.py` (revision read) | `except Exception: return None` | ❌ | reads only | — | ❌ | fail-open — Tier 2 |
| `runtime_revision_store.py` | broad `except Exception` defensive | ⚠️1 | ✅ fsync | ❌ | ❌ | partial — Tier 2 |
| `mountpoint_allocator.py` | raises (uncaught) | ✅11 | ✅ replace, ❌ fsync | ❌ | ✅2 | fsync gap — Tier 3 |
| `recovery_persistence.py` | handled | ✅3 | ✅ | ❌ | ✅3 | mostly done — Tier 3 |

**No store does a directory fsync** (rename durability gap, universal). There are **3 duplicated
`_atomic_write*`** helpers (`runtime_revision_store`, `runtime_env_writer`, `system.atomic_write_text`)
+ **2 quarantine helpers** (`operation_journal`, `state_file`) — consolidation opportunity, not a rewrite.

### The confirmed production risk (the headline)
`stream_binding_store/nodes.py:92` — `agent_token = existing.get("agent_token") or
_read_secrets(state_path).get(node_id) or mint_agent_token()`. A corrupt `node_secrets.json` →
`_read_secrets` returns `{}` (fail-open, secrets.py:30) → `.get(node_id)` is None → **mints a NEW
token** + persists it. The node-agent still holds the OLD bearer token → **control-plane ↔ node auth
mismatch** (the node is now locked out / silently re-enrolled). This is the audit's High finding, exact.

`secret_store.py` is worse on durability: `rotate()`/`set_field()` do a read-modify-write with NO flock
(two concurrent rotations lost-update each other) and `os.rename` with NO fsync (power-loss torn write);
`_parse_env_file` silently skips malformed lines so a damaged `camera-secrets.env` shows keys as `[unset]`.

## Scope — Tier 1 first (the two secret stores)
**`secret_store.py`** + **`stream_binding_store/secrets.py`**. These hold bearer secrets (CAM_ADMIN_TOKEN,
TURN/Janus secrets, per-node agent tokens) — the near-Critical ones. Tier 2 (`runtime_config_apply`
read, `runtime_revision_store` defensive catches, `mountpoint_allocator` fsync) is a documented
follow-up sub-cycle, not this one (минимальный refactor; one thing at a time).

## Plan — sub-commits (tests-first, suite green between)
1. **char** — characterization tests on the CURRENT behavior (the user's matrix): corrupt JSON / corrupt
   env line / unreadable (perm) file / missing file (legit empty) / concurrent update / partial write.
   Pin today's fail-open behavior RED-on-intent, then flip to fail-closed in step 3.
2. **shared util** — one small `atomic_write(path, data, *, mode)` (tmp → fsync(file) → os.replace →
   **fsync(dir)**) + a shared `quarantine_corrupt(path, reason)` (reuse the state_file/journal pattern).
   Used by the Tier-1 stores; existing helpers left in place (no forced migration).
3. **harden Tier 1** —
   - `stream_binding_store/secrets.py`: `_read_secrets` corrupt → quarantine + raise a domain
     `SecretsStoreCorrupt` (NOT `{}`); `_set/_remove` wrap the RMW in an flock + use the shared atomic
     write (adds dir-fsync). Caller `nodes.py` distinguishes **token-absent (legit mint)** from
     **store-corrupt (fail closed — surface error, DO NOT mint)** — kills the regen/mismatch.
   - `secret_store.py`: flock around `rotate`/`set_field` RMW; shared atomic write (fsync + dir-fsync);
     corrupt/undecodable file → quarantine + raise (not partial). (See D2 for the env-line nuance.)
4. **guard** — a fitness guard: no secret/config store read returns `{}`/`[]`/`None` on a
   decode/OS error (i.e. no fail-open `except (JSONDecodeError|OSError): return {}` in the store set).

## Open decisions to gate (GO before any code)
- **D1 — scope:** Tier 1 only (the two secret stores) this cycle vs Tier 1+2. **Lean: Tier 1.**
- **D2 — `secret_store.py` env-parse strictness:** it is a line-based env file, not JSON. Fail-closed on
  an **undecodable / non-empty-but-zero-parseable** file (quarantine+raise), but KEEP lenient single-line
  skip (a stray line ≠ corruption) + log a warning. vs strict (any non-KEY/non-comment line = corrupt).
  **Lean: the former** (durability+concurrency are the real risks; don't over-quarantine env files).
- **D3 — shared util vs inline:** one shared `atomic_write`+`quarantine_corrupt` reused by Tier 1 (don't
  force-migrate the other stores) vs inline per store. **Lean: shared util, Tier-1-only adoption.**
- **D4 — secrets.py caller:** `nodes.py` must treat a `SecretsStoreCorrupt` as fail-closed (surface /
  degrade) and NOT fall through to `mint_agent_token()`. **Lean: yes — that IS the fix.** (Token genuinely
  absent for a *new* node still mints, as today.)

## Red lines
No store-layer rewrite. No enterprise repository pattern. Reuse the H-02/H3 quarantine + flock + atomic
pattern already in the tree. Don't change public DTUs/response shapes unless a test reveals a real bug.
Don't touch the already-fail-closed stores (state_file, operation_journal). A genuinely-missing file
stays a legit empty (only a *corrupt* file fails closed). Tests-first; never weaken a characterization
assertion. Each sub-commit: full non-e2e suite green.

Expected: closes the audit's secret/config High finding (6.5–7.2 → ~7.1–7.5).

## Status — DONE (2026-06-21)
Decisions taken: **D1** Tier 1 + Tier 2; **D2** flock+fsync, lenient parse (fail-closed only on
undecodable / non-empty-zero-parseable); **D3** one shared stdlib `store_safety` util; **D4** caller stops
minting on corrupt. A KEY refinement surfaced during build: **content corruption fails closed; an
access/IO (permission) read error DEGRADES to empty + warn** — a permission error is not corruption (the
bytes may be fine; the write path fails too → self-correcting), and treating it as corrupt 503'd every
node route in the non-root test env. The user's "structured error / degraded state" framing covers this.
- **1.1** `635fb4f` — `services/store_safety.py` (stdlib-only): `atomic_write_text` (+ the missing
  **dir-fsync** + explicit mode) + `quarantine_corrupt` (idempotent forensic copy, original LEFT) +
  `StoreCorrupt`. 7 tests.
- **1.2** `82144e3` — `stream_binding_store/secrets.py` (node_secrets.json — the **High finding**): corrupt
  content → quarantine + raise StoreCorruptionError (not `{}`), so `nodes.py:92` stops falling through to
  `mint_agent_token()` → no silent re-enrollment / token mismatch. flock'd RMW; access error degrades.
- **1.3** `f47344a` (+ `f62b827` concurrency tests) — `secret_store.py` (camera-secrets.env): flock around
  rotate/set_field; durable write (fsync+dir, 0600); fail-closed on undecodable / non-empty-zero-parseable,
  lenient on a stray line (D2); app-level `StoreCorrupt → 503` handler. Threaded concurrent-RMW tests.
- **1.4** `8c8b90d` — Tier 2: `runtime_config_apply._read_raw_revision` (corrupt revision was hidden as
  None / "not found" → now quarantine + StoreCorrupt); `runtime_revision_store.get_revision`/`set_status`
  via a shared `_read_revision_json` (fail-closed); `mountpoint_allocator` write → `atomic_write_text`
  (adds the missing fsync — its deliberate non-dict→{} fail-SAFE read is intentionally unchanged).
- **1.5** (this) — fitness guard **#18** `test_stores_do_not_fail_open_on_corruption`: AST scan of the
  secret/revision/topology store set bans `except <content-error>: return {}/[]/None` (allows pure
  `except OSError` degrade). **The guard caught a real fail-open** I'd missed (`mountpoint_allocator`
  read lookups) — correctly EXCLUDED as the deliberate fail-safe operational store (documented in the
  guard). **18 fitness guards.**

**Result:** a corrupt secret/config store no longer reads as empty → no silent secret regeneration / lost
update / control-plane↔node mismatch / corrupt-revision-as-not-found. All such stores fail closed with a
quarantined forensic copy, flock-serialised RMW, and dir-fsync durability. **Tier 2 follow-up:** none
outstanding from this cycle. Backlog (separate cycles): Cycle 2 service-control consistency, Cycle 3
runtime-config truth, Cycle 4 tracked tasks, Cycle 5 stream_bindings route split, Cycle 6 services decomp.
