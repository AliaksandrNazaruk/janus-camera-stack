# StreamBinding Model — Universal Gateway Stream Identity

- **Status:** ✅ **IMPLEMENTED** (G1, commit `69ee581`) — was DESIGN v2 (post-adversarial-review) · 2026-06-18
- **Node:** `.10` gateway (192.168.1.10)
- **Sprint:** G0 spec → built in **G1** (StreamBindingStore); foundation consumed by G2–G6.
- **Prime directive:** do **not** break the live local streams `cam10:{color,depth,ir1,ir2}` (color pinned mp 1305 / port 5004; depth/IR dynamic 1306–1999 / 5006–5099).
- **v2 delta:** multi-sensor (was color-only); local bindings are **projections** over the existing allocator (resolves node_id→serial); **one** free-list (resolves double-allocation); minimal `nodes` table (single SoT for host); `mode` is the structural safety cap; collapsed redundant host fields; status lifecycle; OPEN-Q1/Q2/Q3 closed.

> **Implementation status:** built in G1 (`69ee581`) — `app/services/stream_binding_store.py` (+ `tests/test_stream_binding_store.py`) with the `ensure()` clobber guard in `app/services/mountpoint_allocator.py`. Node table, `ordinal`-based remote windows (`REMOTE_MP_MIN=2000`/`REMOTE_PORT_MIN=5100`, 100-wide), `status` field, local-as-projection, single union free-list, and all §3 validators are live. The `/nodes` CRUD API shipped in **G6** (`77a43b8`). OPEN-Q1/Q2/Q3 resolved as built. **Deferred:** OPEN-Q4 (`desired_active` per remote binding — not added), OPEN-Q5 (firewall ownership → GATEWAY), `StreamTransport.srtp` is a `null` placeholder. **Doc nuance:** the API models in `app/routes/stream_bindings.py` are plain pydantic `BaseModel` with field bounds/patterns — they do *not* set `extra="forbid"` (the §3 "extra=forbid" line describes intent, not what shipped).

---

## 1. Problem

`.10` models streams as **local sensors on local hardware**, not as **bindings** that may be local *or* remote:

- `device_registry.get_registry()` discovers RealSense devices via `pyrealsense2`, single local D435i ("only first D435i provisionable", `device_registry.py:104-110`).
- `mountpoint_allocator` keys on **hardware serial**: `"{serial}:{sensor}" → Allocation(mp_id, rtp_port, desired_active)` in `/var/lib/camera-fdir/sensor_allocations.json` (`mountpoint_allocator.py:60,68-89`). Color pinned `1305/5004` (`:57-58`); depth/IR allocate from pools `1306–1999` / `5006–5099` (`:48-51`); even-RTP/odd-RTCP via `_pick_free` (`:167-182`). **All four sensors are live today** (`sensor_lifecycle.py:46-51`), each with its own `rs-stream@{sensor}` consumer and `rs-{sensor}.contract.env`.
- RTP is loopback end-to-end.

No first-class object says *"this stream comes from node X at host H and lands on Janus mountpoint M."*

## 2. The model (v2)

The store file `/var/lib/camera-fdir/stream_bindings.json` (env `CAM_STREAM_BINDINGS_PATH`) holds **two** maps:

```json
{
  "version": 1,
  "nodes": {
    "cam10": { "host": "127.0.0.1",    "role": "gateway_camera"   },
    "cam55": { "host": "192.168.1.55", "role": "remote_producer", "reachability": "unknown", "ordinal": 0 }
  },
  "bindings": {
    "cam55:color": {
      "binding_id": "cam55:color", "node_id": "cam55", "sensor": "color",
      "mode": "remote_producer",
      "transport": { "rtp_port": 5100, "payload_type": 96, "codec": "h264", "srtp": null },
      "janus":     { "mountpoint_id": 2000, "rtp_iface": "192.168.1.10" },
      "fdir":      { "enabled": true, "policy": "stream_default" },
      "status":    "configured_offline"
    }
  }
}
```

### `nodes` (single source of truth for host) — closes OPEN-Q3
`node_id → { host, role, reachability, ordinal }`. `node_host` lives here **once per node**, never copied into each binding (a node's `cam55:color` and `cam55:depth` cannot disagree on host). `ordinal` is assigned on first `upsert_node` (stable across other nodes' removal) and drives the remote allocation window (§5). The minimal table landed in **G1**; the full `/nodes` CRUD API shipped in **G6** (`app/routes/stream_bindings.py`).

### `bindings` — **remote rows are authoritative; local rows are projections**  — closes OPEN-Q2
- **Remote** (`mode=remote_producer`): authoritative stored rows. No local hardware/serial backing. Authoritative **from G2** (they drive Janus creation and FDIR enumeration). This is the only coherent reading of "first GO = G1+G2" (see §5).
- **Local** (`mode=local_producer`): **read-only projections** computed from `mountpoint_allocator.list_allocations()` at read time — *not* stored rows. All serial-keyed allocations belong to the single local node `cam10` (today only one local D435i is provisionable), so every `"{serial}:{sensor}"` allocation projects to `binding_id="cam10:{sensor}"`, folding the serial. This **removes the node_id→serial problem entirely** (we map *all* local serials → `cam10`, never `cam10` → a serial).

### Sub-objects & derivations (host fields collapsed) — closes R-M1/R-M4
- `StreamMode = local_producer | remote_producer` (**required**; never defaulted — see §3).
- `StreamTransport { rtp_port, payload_type, codec, srtp? }`. The producer's **destination host** is *not* a stored field: it equals `janus.rtp_iface` (the producer must target where Janus listens). The **expected source host** (for the firewall) is `nodes[node_id].host`. Carrying only `rtp_port` here removes the four-host redundancy v1 had and the two equalities nothing enforced.
- `StreamJanusConfig { mountpoint_id, rtp_iface }`.
- `StreamFdirConfig { enabled, policy }` — `policy` is a **within-mode** tuning knob, *not* the safety discriminator (see §4).
- `status ∈ {configured_offline, waiting_for_rtp, online, stale, degraded}` — runtime lifecycle (the user's G3 states). FDIR writes `stale`/`degraded` (UNIFIED §4); G3 registry reads it.

## 3. Field rules (house style + safety)

- Persistence: frozen dataclasses with `to_dict()`/`from_raw()` (like `Allocation`, `mountpoint_allocator.py:68-89`). API boundary: Pydantic `extra="forbid"` + `Literal`/`Field` + `@model_validator` (like `runtime_schema.py:63-83`).
- **Required (reject on absence, never default):** `binding_id`, `node_id`, `sensor`, `mode`, `mountpoint_id`, `rtp_port`. Defaulting `mode` is forbidden — a stale row defaulting to `local_producer` would run local recovery on a remote fault (the exact UNIFIED §0 violation).
- **Validators:**
  - `mode=remote_producer` ⇒ `nodes[node_id].host` is a non-loopback IPv4 LAN address (reject `127.0.0.1`); `rtp_iface` is REQUIRED and must be a `.10` LAN IP within the camera-LAN subnet (no egress-heuristic default for remote — GATEWAY OPEN-Q6/R3-m2).
  - `mode=local_producer` ⇒ host/iface default to `127.0.0.1`.
  - `rtp_port` **even**; the pair `(rtp_port, rtp_port+1)` (RTP+RTCP) is the **uniqueness unit** (R2-m3): no other binding may use either port.
  - `mountpoint_id`, `(rtp_port,rtp_port+1)` unique **across the union of `bindings` and `sensor_allocations.json`** (R2-M1), not just the new store.
  - **Invariant:** no `remote_producer` binding may hold `mountpoint_id == settings.janus_mount_id` (default 1305) — that id is the local watchdog's target (UNIFIED §M3 bypass guard).
  - IPv4 literals only for v1 (hostnames/IPv6 out of scope; v4-guard the `HOST_LAN_IP` default).

## 4. `mode` is the structural safety cap — closes OPEN-Q10 / R4-B4

The recovery-action ceiling keys off **`mode`** (an enum invariant that cannot be misconfigured), not `policy`:

| `mode` | max RecoveryAction | recovery routing |
|--------|--------------------|------------------|
| `local_producer` | full ladder incl. `REBOOT_NODE` | existing local path (`LocalNodeClientAdapter`) |
| `remote_producer` | `{mark_degraded, emit_alert, NodeClient.restart_stream}` only — **no** local-destructive action | `RemoteNodeClient` (offline stub now) |

`policy="stream_default"` is identical on both examples *because it is not the discriminator* — `mode` is. `policy` may later tune within-mode behavior (intervals, alert thresholds). UNIFIED_FDIR consumes this table verbatim.

## 5. StreamBindingStore + the single free-list — closes OPEN-Q1 / R2-B2/M1/M5

Built on the proven primitives: atomic write = `runtime_revision_store._atomic_write_json` (`:145-163`); lock = `mountpoint_allocator._flock_state` pattern (`:129-164`).

**One free-list, by delegation:**
- **Local** bindings **never** allocate in the new store — they defer entirely to `mountpoint_allocator` (cam10's mountpoint/port space *is* `1305/5004` + pools `1306–1999`/`5006–5099`).
- **Remote** allocation: `allocate_mountpoint(node_id)` / `allocate_port(node_id)` pick from ranges **strictly above the legacy pool** — `mp ≥ 2000` (above `MP_ID_MAX=1999`), `port ≥ 5100` (above `PORT_MAX=5099`). Per-node 100-wide windows keyed by node `ordinal`: node N → mp `[2000+100·N .. +100)`, ports `[5100+100·N .. +100)` (even base). So the **first** remote node (`cam55`, ordinal 0) → mp `2000–2099` / ports `5100–5199` (first allocation 2000 / 5100). The check scans the **union** of `bindings` *and* `sensor_allocations.json` used-sets.
- **Lock ordering** for any cross-store read: take the allocator lock **then** the bindings lock (prevents the TOCTOU double-grant). `allocate_*` reads the allocator's used-set under that order.

**API:** `list()` (merge: projected-local + stored-remote), `get(binding_id)`, `upsert(binding)` (remote only; validates §3 incl. cross-store uniqueness), `remove(binding_id)`, `allocate_port(node_id)`, `allocate_mountpoint(node_id)`, `set_status(binding_id, status)`.

**"Shadow" defined (R2-M3):** in G1, local projections are **read-only / telemetry+UI only** — nothing acts on them; the live path keeps using `sensor_lifecycle`+`mountpoint_allocator` unchanged. Remote rows are authoritative from G2. There is **no** local cutover in G1/G2; folding the serial allocator under the model is a later, separate sprint.

**Prerequisite fix (R2-M2) — DONE in G1:** `mountpoint_allocator.ensure()` now rejects a pre-determined `(mp_id, port)` already held by a *different* key (the `serial="unknown"` clobber that would pin two `*:color` rows to `1305/5004`); same-key calls stay idempotent. The local-color projection tie-breaks to the allocation whose `mp_id == janus_mount_id`.

## 6. Fixed points
1. local camera = projection with `node.host=127.0.0.1`. 2. remote camera = stored binding with `node.host=LAN IP`. 3. Janus always on `.10`. 4. rs-stream destination configurable — **app side DONE in G2** (`sensor_lifecycle._write_contract_env` emits `RTP_TARGET_HOST`); the consuming `rs-stream.sh` edit lives in the external encoder repo (`host_infra`). 5. FDIR over `binding_id` (UNIFIED doc — built G5).

## 7. Non-goals
No Janus on remote node · no SSH orchestration · no behavioural change to how `cam10` streams in G1/G2 (projection only) · no UI before store/API · no local cutover in the first GO.

## 8. Remaining open (non-blocking for G1/G2)
- **OPEN-Q4** binding-level `desired_active` boot flag (reuse allocator pattern) — deferrable.
- **OPEN-Q5** firewall rule ownership (helper vs IaC) — GATEWAY doc; deferrable past G2 design.
- **SRTP / ingest-secret** field shape in `StreamTransport.srtp` — GATEWAY §security; the field exists (`null` for now), spec'd when remote ingest hardening lands.

## 9. Acceptance (G1) — observable assertions
1. remote binding round-trips; local projection equals the live allocation after each `initialize/stop` (assert projection == `get_allocation(serial,sensor)`).
2. `upsert` rejects: duplicate `mountpoint_id` or `(rtp_port,rtp_port+1)` **vs the union** of both stores; odd `rtp_port`; missing `mode`/`node_id`; remote with loopback host; remote with `mountpoint_id==janus_mount_id`.
3. local mode defaults host/iface to `127.0.0.1`; remote requires LAN host + explicit `rtp_iface`.
4. **cam10 unaffected:** G1 introduces **zero** new writes on the live path — assert no write to `sensor_allocations.json` from the bindings store, writes only to `stream_bindings.json`.
5. multi-sensor: a `cam10:depth` projection is produced from a dynamic depth allocation (not just color).
