# Unified node lifecycle — local (.10) and remote (.55) differ ONLY by transport

**Status:** RECON + design proposal. No production code. Awaiting GO on the gate (§8).
**Principle (owner):** a node is a node. `.10` (local) and `.55` (remote) should obey the **same
desired-state contract and lifecycle**; the *only* difference is transport (in-process/local vs
SSH+HTTP). Today they diverge in how streams are brought up, which is the root of "the `.55` cameras
work but the gateway shows them stopped / FDIR-disabled."

---

## 1. The asymmetry, from code

| Aspect | `.10` (local) | `.55` (remote) |
|---|---|---|
| encoder unit | `rs-stream@color` **disabled** (`systemctl is-enabled` → disabled) | bootstrap `systemctl enable --now rs-stream@${SENSOR}` (`bootstrap.sh:217`) → **autostart at boot** |
| what brings a stream up | `sensor-reconcile.service` (oneshot, **enabled**) reads `desired_active` and starts ONLY desired streams (`app/tools/sensor_reconcile.py`) | node systemd autostart (always) + imperative `activate_streams` over SSH (`node_provisioner.py:167` → `transport.run(...)`) |
| desired-state source | `mountpoint_allocator.desired_active` — a real per-sensor flag, the documented boot-lifecycle source of truth | **none** — remote bindings have no `desired_active` field (`/stream-bindings` shows `desired=None`); "desired" is implicitly `fdir.enabled` |
| Start / Stop semantics | desired_active on/off; reconcile converges | `fdir.enabled=False` == "Operator Stop" (`binding_provision.py:144`) — conflated with FDIR |
| after a node reboot | brings up exactly the desired set | brings up whatever was `enable`-d at bootstrap (ignores gateway intent) |
| model | **declarative** (converge to desired) | **imperative + autostart** (run commands; node runs on its own) |

**Consequence:** the `.55` node streams independently of the gateway (its own systemd), so the gateway
can mark a binding "stopped" (`fdir.enabled=False`) while the node keeps producing RTP. The gateway and
node disagree, and the only "desired" lever for remote (`fdir.enabled`) is overloaded with FDIR.

## 2. Root cause

The **gateway** was universalized (one `StreamBinding` store + `ui_viewmodel` for both nodes — see
`project_gateway_universalization`), but the **node-side lifecycle was not**. Local already runs the
declarative `desired_active` → `sensor-reconcile` model; remote still runs the pre-universalization
autostart model. Two missing pieces:
1. The remote node has **no node-side reconciler** and **no node-visible desired state** — it can't
   "bring up exactly what the gateway wants."
2. The binding store has **no remote `desired_up`** field; `fdir.enabled` does double duty
   (Stop + FDIR), so you cannot express "up but unmanaged" or "stopped but observed," and Start/Stop
   can't be separated from recovery.

## 3. Target model (symmetry)

One contract for both nodes; transport is the only difference.

- **Desired state** is per (node, sensor) in the gateway store: `desired_up: bool`. `fdir.enabled`
  becomes purely "auto-recover this binding," orthogonal to up/down.
- **Each node runs the same reconciler** (`sensor-reconcile`): bring up exactly the streams the
  gateway desires; nothing is `systemctl enable`-d for autostart. Local reads the allocator; remote
  reads the gateway-provided desired (pushed to the node, or pulled by the node-agent).
- **Start/Stop** = set `desired_up`; the node converges. **FDIR** = recovery when actual drifts from
  desired — same ladder semantics for both, gated by `fdir.enabled`. (The first implementation briefly
  gated the remote converge on `desired_up` alone — "FDIR off still recovered"; realigned to this
  note's intent in `docs/design/FDIR_RECOVERY_SEMANTICS.md`: converge gates on `desired_up AND
  fdir.enabled`.)
- **Gateway reconcile** (`binding_provision.reconcile_janus`) ensures the Janus mountpoint for every
  `desired_up` binding regardless of `fdir.enabled` (today it skips `fdir.enabled=False`, which is why
  `.55` mountpoints dropped on restart).

Net: "Start a stream" → desired_up=true → node-reconcile brings the encoder up + gateway ensures the
mountpoint + FDIR (if enabled) keeps it healthy — identical on `.10` and `.55`.

## 4. Concrete changes (spans 4 layers + a redeploy)

1. **Store schema:** add `desired_up` to `StreamBinding` (default true on create/activate);
   back-compat read (absent → derive from `fdir.enabled` so existing files don't change behavior).
   Keep `fdir.enabled` = recovery only.
2. **Gateway:**
   - Split Start/Stop (sets `desired_up`) from the FDIR toggle (`fdir.enabled`).
   - `reconcile_janus` + `remote_stream_monitor`: gate mountpoint-ensure on `desired_up` (not
     `fdir.enabled`); gate alert/recovery on `fdir.enabled` (the Cycle-just-shipped status-observe
     decouple is the first half of this).
   - Expose desired to the node (push on change via the agent, or serve it for the node to pull).
3. **Node bundle (`.55`):**
   - Stop `systemctl enable`-ing `rs-stream@` for autostart (`bootstrap.sh`); ship a node-side
     reconciler (the `sensor_reconcile` logic, bundle-local / app-dep-free) that reads the
     gateway-provided desired and converges.
   - Node agent gains a "set/get desired" endpoint (or the reconciler pulls from the gateway).
4. **Console/UI:** Start/Stop reflects `desired_up`; FDIR column reflects `fdir.enabled` only — no
   longer conflated (fixes the "live stream shown FDIR-disabled" confusion).
5. **Deploy:** rebuild + push the node bundle to `.55`; one gateway restart.

## 5. Migration / compatibility (must not break `.10` or running `.55`)

- `.10` already IS the target model — it should be a no-op there (reuse `sensor_reconcile` +
  `desired_active`; map `desired_up`→ the allocator flag for local).
- Phase it: (a) store `desired_up` additive + back-compat default; (b) gateway gates on `desired_up`
  with `fdir.enabled` fallback so behavior is identical until the node side ships; (c) node-side
  reconciler + drop autostart, redeploy `.55`; (d) flip Start/Stop UI to `desired_up`.
- A node that hasn't received desired yet must fail SAFE (keep current streams; don't tear down live
  encoders) — mirrors the allocator's fail-safe stance.

## 6. Risks / red lines

- **Don't tear down live `.55` streams during migration** — the node-reconcile must converge
  additively, never stop a producing encoder just because desired hasn't propagated.
- **Don't regress `.10`** — local is the reference; the unification must reduce to today's behavior
  there (the suite + fitness guards must stay green).
- Keep the FDIR **safety boundary** (a remote fault never drives a local-destructive action) intact —
  unchanged by this; `fdir.enabled` still gates recovery.
- No new generic framework; reuse `sensor_reconcile`, the binding store, the node agent.
- Recon-only until GO; node-bundle + store schema changes are behind the gate.

## 7. What this fixes (the symptoms that led here)

- `.55` "works but shows stopped / FDIR-disabled" → Start = desired_up; node converges; status honest.
- `.55` mountpoints dropping on gateway restart → reconcile ensures every `desired_up` binding.
- "Why isn't FDIR auto-on when streams start" → Start sets desired_up and FDIR is independently on by
  default; no manual toggle, no autostart/desired conflict.

## 8. Gate decisions

- **D1 — bring-up driver. RESOLVED: gateway-driven (not node-pull).** The node will NOT autostart;
  the GATEWAY brings up each desired_up + FDIR stream when the node is REACHABLE ("FDIR brings it up
  when the node IP is available again"). Rationale: the gateway already owns the Janus mountpoints and
  is always the orchestrator — if the gateway is down, there is nowhere to stream anyway, so a node
  that converges on its own buys nothing. This also avoids a node→gateway pull-auth path: the gateway
  reaches the agent with the existing per-node token. **Shipped (gateway side):** the remote monitor
  now starts a never-yet-healthy desired_up+FDIR stream on a reachable node (`_node_reachable` probe +
  throttled ensure/restart). Remaining: drop `systemctl enable rs-stream@` autostart in the node
  bundle + redeploy `.55`.
- **D2 — flag split.** (a) **Add `desired_up`, make `fdir.enabled` recovery-only** [recommended].
  (b) keep one flag, just decouple in behavior (less clear, keeps the conflation).
- **D3 — rollout scope of the first cycle.** (a) **Gateway + store only** (additive `desired_up`,
  gate reconcile on it, split UI) — leaves `.55` autostart for a follow-up [recommended: smallest
  safe first step, no node redeploy]. (b) full end-to-end incl. node-bundle reconciler + drop
  autostart + redeploy `.55` in one cycle (bigger, riskier).
- **D4 — local mapping.** Confirm `desired_up` for local maps to `allocator.desired_active` (one
  source of truth) rather than introducing a second local flag.
