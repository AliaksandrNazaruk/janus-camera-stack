# FDIR owns recovery (model B) — realign converge gate with the FDIR name

**Status:** proposed (recon done; awaiting GO before code). Supersedes the model-A split in
`UNIFIED_NODE_LIFECYCLE.md` for the *recovery* axis only.

## Why

`fdir.enabled` is the **F**ault **D**etection **I**solation **R**ecovery toggle — the name owns
recovery. The unified-lifecycle work (model A) moved the remote **convergence/recovery** action
(ensure mountpoint + restart the node encoder) onto `desired_up` and left `fdir.enabled` gating
only the *alert/escalation*. Consequence an operator hit live: `.55` showed **FDIR disabled** yet
the gateway still auto-restarted its streams after a node reboot. That contradicts the name and the
operator's mental model ("FDIR off ⇒ stop auto-recovering this") — and our own earlier intent
("фдир пусть поднимет когда ип ноды будет снова доступен").

The code is already self-contradictory: `stop_binding.py` and guard #28 **text** describe model B
("monitor gates recovery on `desired_up AND fdir.enabled`"), while `remote_stream_monitor.py:207`
implements model A (`converge = desired_up …`, no `fdir.enabled`). This note picks B and makes the
code match its own docs.

## The model (two independent axes — unchanged shape, recovery re-homed)

| axis | flag | meaning |
|---|---|---|
| **wanted up?** | `desired_up` | operator Start/Stop. Gateway **maintains the Janus mountpoint** (the listener) for every `desired_up` binding — survives gateway restart. |
| **auto-managed?** | `fdir.enabled` | autonomous keep-alive: **detect + recover** (bring-up / restart the producer) **+ escalate** (alert). Off ⇒ not auto-managed. |

### Behavior matrix (remote producer)

| `desired_up` | `fdir.enabled` | mountpoint | auto bring-up / restart | alert |
|:-:|:-:|:-:|:-:|:-:|
| true | true | ✅ kept | ✅ converge (the normal "on") | ✅ |
| true | **false** | ✅ kept | ❌ **manual only** (Restart) | ❌ |
| false | — | ❌ removed | ❌ | ❌ |

`desired_up=true, fdir=false` is a coherent state: "I want this stream, keep its mountpoint, but
**I'll manage it by hand** — don't auto-restart it." The UI column "FDIR disabled" now reads true:
not auto-managed.

## The change (surgical)

1. `remote_stream_monitor.py` — `converge` gate gains `and b.fdir.enabled`:
   ```python
   converge = (b.desired_up and b.fdir.enabled and not healthy_now and converge_due
               and _node_reachable(nodes.get(b.node_id)))
   ```
   Update the in-file comment block (CONVERGE now gated on `desired_up AND fdir.enabled`) + module
   docstring + `_apply` docstring.
2. **Unchanged:** `binding_provision.reconcile_janus` keeps gating mountpoint maintenance on
   `desired_up` (correct in B). `escalate` already gates on `desired_up AND fdir.enabled`.
3. **Guard #28** — give (b) teeth: assert the monitor's `converge` line requires `fdir.enabled`
   (not just that the word `desired_up` appears). Update the guard prose.
4. **Local path — out of scope:** local cam10 recovery is the always-on `recovery_ladder`/watchdog
   (`runtime_config_validator`: local `fdir_enabled` = "always-on; no disable flag"). The per-binding
   toggle is a remote concept; local stays always-on. (Asymmetry is pre-existing + documented.)

## Migration / live

Under B, `.55` with `fdir.enabled=False` will **no longer auto-recover**. To keep `.55` autonomous
(its desired state), **enable FDIR on `.55` color/depth** as part of the deploy. After that, a node
reboot path is exactly the earlier intent: node back → reachable → `desired_up ∧ fdir.enabled ∧
not healthy` → restart_stream → up.

## Tests

Flip the model-A monitor cases: `fdir=false + desired_up + unhealthy + reachable` ⇒ **no recover**
(was: recover). Add `fdir=true` ⇒ recover. Escalation cases unchanged. Mountpoint-maintenance
(reconcile) cases unchanged.

## Reversibility

One-line gate + comments/docs/guard. Revert = drop `and b.fdir.enabled`. No schema/data change
(`fdir.enabled` already persisted). `.55` FDIR-enable is a normal store mutation.
