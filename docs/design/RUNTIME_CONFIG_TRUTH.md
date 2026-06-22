# RUNTIME_CONFIG_TRUTH — Cycle 3 recon + plan (GATED, no code yet)

Closes the audit's contract-drift finding: the `/api/v1/admin/runtime-config/apply` capability is
described inconsistently across code, the capability report, route docs, design docs, and the operator
runbook. An API client / operator gets an unclear "what can I apply" model. Cycle 3 picks ONE canonical
truth and synchronises every surface to it + a guard. No code until GO.

## Recon — the drift, layer by layer (verified 2026-06-21)
**The apply engine + route ARE built and tested:**
- `services/runtime_config_apply.py` — "AE-1 — the NEW_SESSIONS_ONLY apply orchestration"; `apply_revision`
  / `_apply_under_lock` write rs-runtime.env + refresh settings + verify + roll back.
- `routes/runtime_config.py:127` — `POST /apply` fully wired (Outcome → HTTP map; "Apply a validated
  NEW_SESSIONS_ONLY revision").
- `tests/test_runtime_apply_ae1.py` — exercises `apply_revision()` incl. the HAPPY PATH + endpoint wiring.
- `docs/OPERATOR_RUNBOOK.md:160,201` — "POST /apply is **live** for the NEW_SESSIONS_ONLY class only".

**But the capability SURFACE says apply doesn't exist / isn't supported:**
- `services/runtime_revision_store.capability_report()` — hardcodes `"apply_supported": False`,
  `"supported_steps": ["journal_only"]`, and a NEW_SESSIONS_ONLY blocker `"awaiting the B2 apply engine —
  B2-0 is journal-only"` (line 311/313). **This is stale code** — the engine (AE-1) landed.
- `routes/runtime_config.py`: module docstring "B1 ... There is NO apply/write/restart endpoint in B1"
  (1,8); `/validate` desc "There is no apply endpoint in B1" (75); `/capabilities` desc "whether runtime
  apply is supported (it is NOT in B2-0)" (100); `/revisions` desc "no apply/rollback in B2-0" (116).
- `runtime_revision_store.is_applyable` docstring "the FUTURE ... apply engine (AE-0 primitive — there is
  NO /apply endpoint yet)" (231).
- `docs/design/B1_RUNTIME_CONFIG.md:186` "No apply endpoint exists in B1."
- Tests assert the stale state: `test_runtime_config_b2_0` `apply_supported is False` with the comment
  "apply_supported STILL false (the B2 engine isn't built)"; a per-revision record carries `apply_supported:
  False`. These pin the drift.

So the surfaces split into TWO camps that contradict each other. This is not a one-line doc fix — the
capability REPORT itself (the operator's machine-readable source of truth) lies relative to the live route.

## The canonical-truth decision (D1 — gate this FIRST)
- **(A) Apply is LIVE for NEW_SESSIONS_ONLY** (the AE-1 engine + route + happy-path tests + runbook are the
  truth; the capability surface is stale). → `capability_report.apply_supported` becomes the LIVE answer
  (dynamic: true when a NEW_SESSIONS_ONLY revision is applyable given the C1 frozen-literal + C2 rs-runtime.env
  blockers; the "awaiting the B2 apply engine" blocker is REMOVED — it landed). Route/store docstrings + the
  B2-0 tests + the design-doc lines updated to "apply is live (NEW_SESSIONS_ONLY); other classes refused."
- **(B) Apply is intentionally NOT advertised yet** (the route/engine exist but are experimental/gated; the
  conservative `apply_supported: False` is deliberate). → the RUNBOOK ("live") + the route `/apply` summary
  are the drift; mark `/apply` experimental and make the docstrings consistent with the report. Keep
  `apply_supported: False` but stop saying "no apply ENDPOINT" (the endpoint exists; it's just gated).

I lean **(A)** — AE-1's happy-path test + the runbook are strong evidence apply is intended-live, and the
"awaiting the B2 apply engine" blocker is provably stale (the engine is in the tree). But this flips an API
field + rewrites the B2-0 capability tests, so it is genuinely your call.

## Plan — sub-commits (tests-first, suite green between) — assuming (A)
1. **char** — a NEW test pinning the DESIRED truth: with C1+C2 cleared, `capability_report.apply_supported`
   is True and NEW_SESSIONS_ONLY has no blockers; the registered routes include `POST /apply`. (RED until 2.)
2. **capability_report truth** — `apply_supported` dynamic (true iff NEW_SESSIONS_ONLY applyable: C1 + C2
   cleared); drop the stale "awaiting the B2 apply engine" blocker; per-revision `apply_supported` likewise.
   Re-point the B2-0 capability tests (identical structure, corrected expectation).
3. **docstring/doc sync** — route module + `/validate` + `/capabilities` + `/revisions` descriptions,
   `is_applyable` docstring, `B1_RUNTIME_CONFIG.md` note → "apply is live for NEW_SESSIONS_ONLY (AE-1);
   other classes refused". (No behavior change — text only.)
4. **guard** — fitness guard: capability-vs-routes consistency. If `POST /apply` is a registered route, no
   production runtime-config docstring/description may claim "no apply endpoint", AND a capability test
   asserts `apply_supported` tracks the live applyability (no hardcoded False while the route is live).

## Open decisions to gate
- **D1 — canonical truth (A) live vs (B) experimental.** (Lean A — but yours to decide; it flips an API field.)
- **D2 — `apply_supported` dynamic** (true iff NEW_SESSIONS_ONLY applyable) **vs a flat True.** (Lean: dynamic —
  it already evaluates C1/C2 live; just stop hardcoding False + drop the stale-engine blocker.)
- **D3 — guard shape:** "registered POST /apply ⇒ no 'no apply endpoint' prose + apply_supported not hard-False"
  vs a lighter "no stale phrase" scan. (Lean: the route-vs-capability consistency test — it's the real invariant.)
- **D4 — scope:** code (capability_report) + docstrings + the B2-0/AE-1 tests + the two design docs + runbook.

## Red lines
Pick ONE truth and make EVERY surface agree (code report, OpenAPI descriptions, store docstrings, README/
runbook, design docs, tests). Behavior change is limited to the capability REPORT's `apply_supported`
field (under A) — the apply ENGINE's actual accept/refuse logic (NEW_SESSIONS_ONLY only, confirm-gated,
verify+rollback) is unchanged. Don't broaden what apply accepts. Tests-first; never weaken an assertion to
hide the drift — fix the assertion to the chosen truth. Full non-e2e suite green per sub-commit.

## Status — DONE (2026-06-21)
Decisions: **D1 (A)** apply is LIVE for NEW_SESSIONS_ONLY (the AE-1 engine + route + happy-path tests +
runbook are the truth; the capability surface was stale). **D2** `apply_supported` DYNAMIC (true iff a
NEW_SESSIONS_ONLY revision is applyable: C1 frozen-literal + C2 rs-runtime.env blockers cleared). **D3**
the route-vs-capability consistency guard. **D4** scope = capability_report + docstrings + the
B2-0/AE-0 tests + the B1 design doc (runbook was already correct).
- **3.1** `90e54f2` — `capability_report()` truth: removed the stale "awaiting the B2 apply engine"
  blocker; `apply_supported = not ns` (dynamic); `supported_steps` includes `"apply"` when supported.
  `persist_validated()` per-revision `apply_supported = is_applyable(record)[0]` (was hardcoded False).
  3 B2-0 tests re-pointed to the corrected truth (one made deterministic via a `RUNTIME_ENV_PATH`
  monkeypatch); the C2-blocked-before-relocation test unchanged.
- **3.2** `a181b39` — doc/docstring sync (text-only): route module + `/validate` + `/capabilities` +
  `/revisions` descriptions; `is_applyable` docstring; the B2-0 + AE-0 test docstrings/comments; and
  `B1_RUNTIME_CONFIG.md:186` — all now say "apply is live for NEW_SESSIONS_ONLY (AE-1); other classes
  refused." No behavior change.
- **3.3** (this) — fitness guard **#20** `test_runtime_config_capability_surface_agrees_with_routes`:
  WHILE POST /apply is a registered route, the runtime-config production source (routes/runtime_config.py
  + services/runtime_revision_store.py) may carry NO stale "no apply endpoint / apply NOT supported /
  awaiting the B2 apply engine" prose, AND `capability_report()` may not hardcode `apply_supported=False`
  (AST). A positive anchor asserts /apply is still registered so the guard fails loudly if the apply
  contract is ever removed. **20 fitness guards.**

**Result:** every surface now tells ONE truth — apply is LIVE for the NEW_SESSIONS_ONLY class (AE-1,
confirm-gated, verify+rollback); the capability report's `apply_supported` tracks live applyability
(C1+C2); other impact classes are refused by the engine. The apply ENGINE's accept/refuse logic is
unchanged — only the capability REPORT stopped lying. Locked by guard #20.
