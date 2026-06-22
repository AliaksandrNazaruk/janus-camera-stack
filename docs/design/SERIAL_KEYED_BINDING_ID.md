# P4 — serial-keyed binding_id (multi-camera-per-host + portable, device-anchored identity)

- **Status:** REMOTE scope DONE + LIVE (`d4dba29`; cam55 migrated `node-…:*`→`048522073892:*` 2026-06-19 with zero stream interruption, firewall re-tagged + persisted). LOCAL/cam10 fold DONE in code (`_project_local` now emits one serial-keyed binding per (serial,sensor) — `{serial}:{sensor}`, node_id stays the `cam10` sentinel — replacing the fold-by-sensor + janus_mount_id tie-break; enables two-D435i-on-.10); **deploy deferred** until the Janus-burst fix is confirmed (it needs an L4 restart, which resets the burst monitors). Builds on [DYNAMIC_CAMERA_ONBOARDING](DYNAMIC_CAMERA_ONBOARDING.md) + [STREAM_BINDING_MODEL](STREAM_BINDING_MODEL.md).
- **Node:** `.10` gateway.

> **Implementation note (refines the premise below).** The *remote* allocation is NOT serial-keyed like the local one — `allocate_mountpoint/allocate_port` are an advisory free-list over the node's **ordinal window**, **derived from the bindings' own stored (mp, port)** (`_used_sets(state["bindings"])`). That makes the migration a safe **key+field rename**: `migrate_remote_binding_ids` rekeys `{node_id}:{sensor}`→`{serial}:{sensor}` preserving the stored mp/port, and the free-list is unchanged (no mountpoint/port churn). Built: `stream_binding_store.remote_binding_id()` (serial, node_id fallback pre-probe) used by `make_gateway_binder` + `POST /stream-bindings`; `migrate_remote_binding_ids()` wired idempotently into `events.py` startup so a redeploy auto-folds; `firewall_sync.reconcile` is now **add-before-remove** so the re-tag has no RTP-drop window. **Not yet done:** the live cam55 migration (redeploy L4 → startup auto-migrates → manual firewall reconcile → verify) + folding cam10's local `cam10:sensor` id.

## Problem
`binding_id` is **node-keyed** today: remote = `f"{node_id}:{sensor}"` (`node_provisioner.py:193`, `routes/stream_bindings.py:368`), local = `f"cam10:{sensor}"` (`stream_binding_store.py:469`). But the **mountpoint allocator is already serial-keyed** — `mountpoint_allocator._key(serial, sensor) = "{serial}:{sensor}"` — and the store comments call out the resulting identity heterogeneity as "the node_id→serial problem (FDIR-KEY-001)". Two consequences:
1. **One camera per host is baked in.** `add_node_by_host` is lookup-or-create *per host* and `NodeEntry.serial` is a single value, so a host with two RealSense devices cannot carry two independent stream sets — both would collide on `node_id:sensor`.
2. **Bindings aren't portable + identity is split.** Move a camera to another host (DHCP re-IP, re-cabling) and its `node_id` changes → new `binding_id` → the firewall/monitor/allocator treat it as a new stream, even though the device (serial) is the same. Meanwhile the allocation that drives the mountpoint/port is keyed by serial, so the two layers disagree.

## Design — make `binding_id = "{serial}:{sensor}"` (align with the allocator)
The serial is the stable device identity and is **known by activation time** (the provisioner probes and calls `set_serial` *before* `activate_streams` creates bindings — `node_provisioner.provision` → `activate_streams`), so the serial is available exactly where binding_id is minted. The allocator already allocates per `(serial, sensor)`, so the mountpoint/port layer needs **no change** — this purely aligns the binding identity to it.

- **Remote `binding_id`:** `"{serial}:{sensor}"`. The `node` becomes purely a **host locator** (where to SSH / where RTP comes from); the binding carries its own `serial`. `StreamBinding` already has a `node_id` field — keep it (for SSH/host routing + firewall source IP) but key identity on serial.
- **Node→serial becomes one-to-many.** A host (`node`) may have N cameras → N serials → N×sensors bindings. `NodeEntry.serial` (single) is replaced/augmented: the node no longer "owns" one serial; bindings reference both `node_id` (host) and `serial` (device). `provision` probes ALL devices (the probe CLI already returns a device list) and records the set; `activate_streams` is invoked per `(serial, sensor)`.
- **Local cam10:** for uniformity, the local projection's `binding_id` also becomes `"{serial}:{sensor}"` (the allocator already keys local allocations by the real serial after `migrate_color_key`), retiring the `cam10:` prefix from identity while `cam10` stays the implicit local *node* sentinel. (Decide: do this in the same change for one identity model, or stage it — leaning "same change" so there is never a mixed scheme.)

## Hard parts (why this is its own change, not a rename)
1. **Migration.** Existing persisted bindings are `node-<uuid>:sensor` (cam55) — must be rewritten to `serial:sensor` once the serial is known, preserving their allocation (the allocator is already serial-keyed, so the allocation row likely already exists under the serial — confirm + reconcile). One-shot, idempotent, like `mountpoint_allocator.migrate_color_key`.
2. **Firewall comments.** `firewall_sync` tags rules `camnode:<binding_id>:<port>` — changing binding_id changes the tags; the reconciler must remove the old-tagged rules and add new ones in one apply (its diff already does add/remove by tag, so a binding_id change looks like remove-old + add-new — verify no window where cam55 RTP is unprotected/dropped).
3. **The monitor + status.** `remote_stream_monitor` keys `_state` + `set_status` by binding_id; in-memory `_state` resets cleanly on restart, but the persisted `status` rows must migrate with the binding_id.
4. **node_client routing.** Recovery routes by `node_id` (host) — unchanged (still need the host to reach the agent). Confirm `get_node_client` stays node-keyed (it should: recovery is host-scoped, identity is device-scoped).
5. **API.** `binding_id` in responses/paths changes shape; `POST /stream-bindings` must accept/derive the serial.

## Acceptance (failure-injection, not happy-path)
- Two serials on one host → two independent binding sets, distinct mountpoints/ports, no collision.
- Re-IP a host (change `node.host`) → binding_id unchanged (device-anchored), firewall source-IP rule updates, stream survives.
- Migration: a pre-change `node-<uuid>:color` binding + its allocation fold to `<serial>:color` idempotently, cam55 keeps streaming across the migration (no mountpoint churn).
- cam10 unaffected (or, if folded, the local projection serves identically under the new key).
- Firewall reconcile across a binding_id change never leaves cam55 RTP dropped (old ACCEPT removed only after new ACCEPT added, or both present transiently).

## Non-goals
- Changing the allocator (already serial-keyed).
- Changing recovery routing (stays host/node-keyed).
