# Allocator write-path corruption recovery — Cycle 15A recon + design note

**Status:** RECON COMPLETE — awaiting GO on a gate decision. No production code yet.
**Scope:** the allocator WRITE path (`_flock_state` + its 5 mutators). Cycle 14A made the
READ path's corruption *observable* and left write behavior unchanged; this cycle decides what a
write should do when the state file is corrupt. **Read fail-safe (guard #26) and `desired_active`
semantics are NOT in scope and must not change.**

---

## 1. Current write-path call graph

All five mutators funnel through one context manager — there is exactly one write chokepoint:

```
migrate_color_key ─┐
allocate ──────────┤
ensure ────────────┼──▶ _flock_state(path)  ──▶  json.loads(raw)   [line 140, UNWRAPPED]
set_desired ───────┤        (flock LOCK_EX)        + shape coercion [lines 143-157]
release ───────────┘        yield state            atomic_write_text(path, json) [line 160]
```

| Mutator | Mutates | Preserves `desired_active`? | Handles invalid JSON? | On truncated/IO file |
|---|---|---|---|---|
| `migrate_color_key` | renames `local:color` → `<serial>:color` | yes (`pop`/reassign) | no (via `_flock_state`) | crash |
| `allocate` | adds a `(mp_id,rtp_port)` entry | yes (returns existing untouched) | no | crash |
| `ensure` | adds a caller-pinned entry (clobber-guarded) | yes | no | crash |
| `set_desired` | flips `desired_active` on an entry | sets it (the point) | no | crash |
| `release` | deletes an entry | n/a | no | crash |

Every mutator inherits `_flock_state`'s behavior verbatim; none has its own corruption handling.

## 2. Current behavior matrix (from code)

`_flock_state` (`mountpoint_allocator.py:129-162`):

| File state | code path | behavior | net effect |
|---|---|---|---|
| missing | 137 → 141-142 | `state = {}` | fresh file written, mutation persists |
| empty file (`""`) | 138-140 (`raw` falsy) | `state = {}` | write OK |
| valid JSON, valid shape | 140 load, 147-157 ok | mutate + write | normal |
| valid JSON, **root not a dict** (`[..]`/`"x"`/`5`) | 143-146 | `log.error` + `state = {}` | **reset + proceed, NO quarantine, silent data loss** |
| valid JSON, **`allocations: null`** | 153-157 (`is None` → no log) | reset `allocations = {}` | **reset + proceed, NO log, NO quarantine, data loss** |
| valid JSON, **`allocations` non-dict** (`"garbage"`) | 153-157 (`log.error`) | reset `allocations = {}` | reset + proceed, logged, no quarantine, data loss |
| **invalid JSON** (truncated bytes) | 140 `json.loads(raw)` | **`JSONDecodeError` propagates** | **CRASH — write aborts; corrupt bytes preserved; no quarantine** |
| **IO read error** (perm/disk) | 138 `open(path,"r")` | **`OSError` propagates** | **CRASH — write aborts** |

**The key defect is an internal inconsistency, not a single bug:** invalid *shape* already
"resets and proceeds" (option-B behavior, pinned by `test_corrupt_allocations_none_does_not_persist`),
but invalid *JSON syntax* **crashes**. The write path half-implements recovery and half-crashes,
and neither branch leaves a forensic trace.

For contrast, the READ path (`get_allocation:191`, `list_allocations:297`, and `_alloc_map`) catches
`(JSONDecodeError, OSError)` → `{}`/`None` for ALL of the above and never raises (guard #26). So a
corrupt allocator reads as empty everywhere, and Cycle-14A's `/readyz allocator_state` correctly
flips to `corrupt`.

### Concrete production impact — the boot reconciler crash

`app/tools/sensor_reconcile.py` runs at boot (`sensor-reconcile.service`, oneshot). On an
**invalid-JSON** allocator file:

1. `seed_if_empty:54` calls `list_allocations` → `{}` (fail-safe, silent) → looks empty.
2. `if existing: return False` is skipped (`{}` is falsy) → it tries to **seed**.
3. `alloc_mod.ensure(LOCAL_SERIAL, "color", …)` → **write** → `_flock_state:140` → `JSONDecodeError`.
4. The exception propagates uncaught through `seed_if_empty` → `main():131` (no try/except) →
   **the reconciler exits non-zero / the service fails. No streams come up.**

So a truncated allocator does not merely "read as empty" — at boot it makes the seed write **crash**
and the stream-bringup service fail. (On invalid *shape* instead, the seed write silently resets and
seeds color — boot continues, but any prior `desired_active` set is lost with no forensic copy.)
Cycle 14A's `/readyz allocator_state=corrupt` is the only signal an operator currently gets; the
boot path itself has no recovery.

## 3. Comparison with `stream_binding_store` corruption handling

`stream_binding_store/state_file.py`:

| Aspect | binding store | allocator |
|---|---|---|
| read on corrupt | **fail-CLOSED** — quarantine + raise `StoreCorruptionError` | **fail-SAFE** — `{}`/`None`, never raise (guard #26) |
| write on corrupt | `_load_state` runs inside `_flock_state` BEFORE yield → raises → **write skipped** | invalid JSON crashes; invalid shape resets+proceeds |
| quarantine | yes — `_quarantine_corrupt_state` → `<path>.corrupt.<ts>`, idempotent, **original left in place** | **none** |
| atomic write | hand-rolled tmp+fsync+replace (no dir fsync) | `store_safety.atomic_write_text` (tmp+fsync+replace+**dir fsync**) |
| write-after-corruption | refused (fail closed) | allowed (shape) / crashes (JSON) |
| recovery model | operator-driven (fix/restore file → ops resume) | none (fail-safe reads hide it; writes crash or silently reset) |

**Do NOT copy the binding store's fail-closed semantics** — the allocator is fail-SAFE by deliberate
design (Cycle 1, guard #18 exclusion) so a corrupt allocator never tears down live encoder streams.
But the binding store proves the *mechanism* we can reuse: `store_safety.quarantine_corrupt(path,
reason)` is **already a shared stdlib primitive** (idempotent `.corrupt.<ts>` copy, leaves original,
best-effort, never raises). The allocator already imports `atomic_write_text` from the same module —
adding a `quarantine_corrupt` import is one line, **no new framework** (constraint #8 honored).

## 4. Risk analysis

- **Data loss.** Invalid-shape writes ALREADY overwrite the corrupt file with a fresh `{}`+mutation —
  silent, no forensic copy (the `null` case doesn't even log). Invalid-JSON writes crash, so the
  bytes survive but no operation can proceed. Either way the operator gets no preserved evidence.
- **Live stream safety.** Reads stay fail-safe → live encoders are never torn down by a corrupt read
  (unchanged, guard #26). A write *crash* doesn't tear down streams either (it just fails the
  mutation). A *reset+proceed* loses the `desired_active` set for live streams — they keep running
  now, but a future boot would not bring them back. That loss already exists for invalid-shape today;
  it is the price of fail-safe operation when the desired set was already unreadable.
- **Operator visibility.** Today: invalid-shape reset is silent-to-quiet (null = no log); invalid-JSON
  = an opaque 500 / boot crash. Quarantine adds a `.corrupt.<ts>` artifact + `log.critical` →
  forensic + greppable.
- **Boot reconcile.** Worst concrete outcome (§2): invalid-JSON → `seed_if_empty` write crash →
  `sensor-reconcile.service` fails → no streams. A non-crashing write (reset+proceed) lets boot
  recover to the seeded baseline instead of failing.
- **Forensic trace.** Currently none. `quarantine_corrupt` is the cheapest way to gain one without
  changing the fail-safe posture.

## 5. Options

- **A — keep current behavior** (invalid JSON crashes). Rejected: leaves the boot-reconcile crash and
  the read/write inconsistency in place; contradicts the Cycle-14A "corruption must be visible" theme.
- **B — wrap `json.loads`, log, reset to `{}`, proceed** (no quarantine). Fixes the crash and makes
  the write path internally consistent (invalid JSON behaves like invalid shape already does). But
  keeps silent data loss — no forensic copy.
- **C — quarantine corrupt file, reset to `{}`, proceed.** B + a `store_safety.quarantine_corrupt`
  call before reset. Fixes the crash, keeps fail-safe (operation proceeds), and preserves a
  `.corrupt.<ts>` forensic copy + `log.critical`. The fresh state is written over the original; the
  forensic copy survives.
- **D — quarantine + raise `AllocationError`.** Forensic, but fail-CLOSED for writes — an operator
  could not change any allocation until they fix the file, and the boot reconciler would still fail.
  Contradicts the fail-safe posture (constraint #3 is about reads, but D imports fail-closed thinking
  into writes). Rejected as the default.
- **E — split invalid JSON vs invalid shape.** invalid JSON → quarantine + reset + proceed; invalid
  shape → log + reset + proceed (today's behavior, optionally + quarantine). Recognizes that invalid
  shape is often a known-soft case (the legacy `allocations: null`) while invalid JSON is hard
  corruption deserving forensics.

**IO error is a separate axis from all of the above.** A permission/disk read error in the write
path means the file may be perfectly good but momentarily unreadable — resetting it to `{}` would
*destroy good data on a transient glitch*, and `quarantine_corrupt` can't copy an unreadable file
anyway. IO error should **raise** (wrapped as `AllocationError`), never reset. This is true under any
of B/C/E.

## 6. Recommendation

**Option C for content corruption (invalid JSON AND invalid shape), uniformly; IO error raises.**

Concretely, in `_flock_state`'s load section:
1. `open`/`read` `OSError` → `raise AllocationError(...)` (do NOT reset — data may be fine).
2. `json.loads` `JSONDecodeError` → `quarantine_corrupt(path, "invalid JSON")` → `state = {}` → proceed.
3. root not a dict / `allocations` not a dict → `quarantine_corrupt(path, "invalid shape")` →
   `state = {}` / `state["allocations"] = {}` → proceed (today's reset, now with a forensic copy).

Why C over the leaner B and the safer-looking D:
- Preserves the allocator's fail-SAFE philosophy (operations proceed; live streams untouched).
- Eliminates the boot-reconcile crash (the concrete production risk).
- Makes the write path internally consistent (one rule for all content corruption).
- Adds the forensic trace the Cycle-14A theme calls for — reusing an existing primitive, no new
  framework, ~5 lines + one import.
- D would re-introduce a fail-closed write path the allocator deliberately avoids; B leaves silent
  data loss.

Uniform-C over E because the split in E buys little: invalid shape already silently loses data, so
giving it the same quarantine+log is strictly more honest, and one rule is easier to guard. (E remains
a valid choice if you want to keep invalid-shape resets quarantine-free for noise reasons — gate D4.)

## 7. Red lines (honored)

- Guard #26 unchanged; the READ helpers keep returning `{}`/`None` and never raise (constraints 1-3).
- No change to `desired_active` semantics (constraint 4) or to the binding store (constraint 5).
- IO error must NOT trigger reset-to-empty (would destroy good data on a transient glitch).
- No new generic store framework (constraint 8) — reuse `store_safety.quarantine_corrupt`.
- No quarantine/write behavior change until GO (constraint 9); no events/lifecycle refactor
  (constraint 7); no G-B drift work (constraint 10).
- `/readyz allocator_state` behavior unchanged unless D2 explicitly opts into an additive field.

## 8. Gate decisions (need GO before any code)

- **D1 — behavior on invalid-JSON write.** (A) keep crash. (B) reset + proceed, no quarantine.
  **(C) quarantine + reset + proceed [recommended].** (D) quarantine + raise `AllocationError`.
  (E) split invalid-JSON vs invalid-shape. *(Under B/C/E, IO error raises `AllocationError`, never
  resets — recommended regardless.)*
- **D2 — should `allocator_corruption_status()` report a recent quarantine?** (A) no — once reset, the
  file is valid so `allocator_state="ok"` is truthful. **(B) include the latest `.corrupt.*` path if
  one exists [recommended] — additive field, keeps `allocator_state` truthful while making the
  forensic discoverable.** (C) sticky `recovered_from_corrupt` until explicitly cleared (needs extra
  state + a clear path — heavier).
- **D3 — new guard after implementation?** (A) none. **(B) guard: allocator write paths must not
  propagate `JSONDecodeError` (re-run a mutator on a truncated file, assert no decode error escapes
  and the mutation persists) [recommended — real teeth].** (C) guard: a `.corrupt.*` artifact is
  created on invalid-JSON recovery. (B+C both have teeth and compose.)
- **D4 — scope.** (A) invalid JSON only. **(B) invalid JSON + invalid shape [recommended — one
  uniform rule].** (C) + IO error *(only as "IO error raises `AllocationError`", NOT as reset)*.
