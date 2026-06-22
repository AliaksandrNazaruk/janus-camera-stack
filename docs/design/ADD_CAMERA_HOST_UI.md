# Add Camera Host — unified onboarding UI (local == remote)

Status: DESIGN (2026-06-19). Implements the operator-facing surface for the
host-agnostic onboarding that the API already supports (see
DYNAMIC_CAMERA_ONBOARDING.md). The G6 admin API (`/api/v1/admin/nodes…`,
`/stream-bindings`) is live; there is **no web page** that drives it today, so
adding a host means hand-rolling `curl`. This closes that gap.

## 1. Principle

> "Добавляем хост — неважно удалённый или локальный."

Adding a camera is **one uniform operator action** regardless of where the
camera physically sits. The local/remote distinction is an *implementation*
detail the system hides — it must not surface as two different UIs or two
different verbs.

## 2. What must NOT change (the hard constraint)

The FDIR safety model deliberately keeps local and remote **structurally
distinct** via `StreamMode`:

- `LOCAL_PRODUCER` — the gateway's own camera. Shares Janus with the control
  plane; the §4.5 shared-Janus reboot guard depends on this mode.
- `REMOTE_PRODUCER` — a camera on another host. Remote-stream monitor + node
  recovery ladder (RESTART_NODE, never REBOOT the gateway for a remote fault).

**This design unifies the UX and the activation verb. It does NOT merge the two
producer modes, the two allocator windows (local <2000, remote ≥2000), or the
two FDIR domains.** A local binding stays `LOCAL_PRODUCER`; a remote binding
stays `REMOTE_PRODUCER`. The unification is at the onboarding layer only.

## 3. The asymmetry we are bridging (today)

| step | local (`cam10`) | remote (node by IP) |
|---|---|---|
| register | implicit sentinel in `list_nodes()` | `POST /nodes` (mints node_id) |
| host-key | n/a | `GET/POST /nodes/{id}/host-key[/confirm]` |
| provision | none (Janus already local) | `POST /nodes/{id}/provision` (SSH push bundle) |
| **activate sensor** | `POST /cameras/{serial}/{sensor}/initialize` | `POST /nodes/{id}/streams` |
| binding | computed projection of allocator | stored row in `stream_bindings.json` |

The activation verb is the only asymmetry the operator should ever feel, and
it is the one we erase.

## 4. Decision — one activation verb, a thin local adapter

**Extend `POST /api/v1/admin/nodes/{node_id}/streams` to accept
`node_id == LOCAL_NODE_ID` (`cam10`).**

- Remote (`node_id != cam10`): unchanged — async SSH transport +
  `activate_streams` (allocate ≥2000 mp / ≥5100 port, ensure-janus on gateway
  LAN IP, node-side `bootstrap activate`). Produces `REMOTE_PRODUCER` bindings;
  returns `{…, started:true, poll:"GET …/stream-bindings"}` and the UI polls.
- Local (`node_id == cam10`): **synchronous** — loops the requested sensors
  calling `sensor_lifecycle.initialize(local_serial, sensor)` (each ~0.3–0.8 s,
  the same call the Devices page already makes synchronously). No SSH, no
  `sudo_password`, no host-key, no bundle. Produces the existing
  `LOCAL_PRODUCER` allocator projection (mp <2000, port <5100, loopback iface).
  Returns `{…, poll:null, results:[{sensor, ok, detail, mountpoint_id?}]}` —
  the terminal per-sensor outcome **in the response**.

> **Why local stays synchronous (adversarial-review C1).** Local bindings are
> read-only projections whose `status` derives from the allocator's
> `desired_active` flag — there is **no `set_status` for a local binding**, and
> for color `desired_active=True` is set *before* the encoder start (for boot
> retry). A fire-and-forget background task would therefore (a) lose any
> `LifecycleError` and (b) render a just-failed color stream as `online`. The
> existing `/cameras/{serial}/{sensor}/initialize` route is already synchronous
> and surfaces errors in the response; we keep that property. **Unify at the
> response/UI layer, not by faking async.** The frontend branches on
> `resp.poll ? startPolling() : renderResults(resp.results)` — still one page,
> one verb, two response readers.

The shared verb does **not** touch binding mode: remote always allocates
≥`REMOTE_MP_MIN` and tags `REMOTE_PRODUCER`; local always projects
`LOCAL_PRODUCER`. The remote-binding reconciler / monitor / firewall all filter
`mode==REMOTE_PRODUCER` *and* re-check `mp<REMOTE_MP_MIN` fail-closed — verified
clean by review. `provision` and `host-key` keep rejecting `cam10` (400) — but
`activate` must branch on `cam10` **before** `_node_for_provision()` (which
400s local and 503s on a missing bundle local doesn't need; review M3).

### 4.1 Resolving the local serial (review H1)

`sensor_lifecycle.initialize` needs the device serial. The **allocator is the
identity of record** for already-onboarded streams, so resolve from it FIRST —
this also avoids the expensive/throwing probe in the common case and dodges the
camera-swap clobber (probe returns serial A while live streams are pinned under
serial B → `ensure()` clobber-guard → uncaught `AllocationError`).

`_local_serial() -> Optional[str]` (returns a *real* serial or `None`; the
`"local"` sentinel is never returned — it is an internal color-only detail):

1. **Existing allocations** — `list_allocations()` keys are `{serial}:{sensor}`;
   if a real (non-`"local"`) serial is present, return it (the cam10 fold wrote
   `141722072135:*`, so this is the steady-state hit). Multiple → the serial
   with the most allocations, ties broken deterministically, logged.
2. **Device registry probe** — only when no allocation carries a real serial
   (fresh box). New `device_registry.local_serial()`: cached, defensive (catches
   the `pyrealsense2`-absent / `rs.context()`-throws / no-camera cases →
   `None`), never on the hot path.
3. `None` — could not resolve.

Per-sensor gate in the activate branch:
- **color** tolerates `None` → passes `LOCAL_SERIAL`; `migrate_color_key`
  reconciles identity (color-only migration exists).
- **depth/ir** with `serial is None` → **409** (`allocate("local", "depth")`
  would write an orphan `local:depth` key that never matches the
  device-serial-keyed identity used by viewers + FDIR). Clear message:
  "attach camera / probe failed — cannot activate depth without a device serial".
- Probe serial vs allocation serial **mismatch** → 409, not a silent clobber.

### 4.2 Why not call `/cameras/.../initialize` from the UI directly?

That would make the unification cosmetic (a host-type `if` in the frontend)
and leave the principle unmet end-to-end. Routing local through the same node
verb means "add a host" is genuinely uniform from the browser to the binding
store, which is the stated original intent.

## 5. UI — `camera_hosts.html`

A standalone page (matches `admin_config.html`: CSP nonces, `camera_config.css`,
`console_lib.js` for `authFetch`/`$`/`setStatus`). Vanilla-JS IIFE in
`static/js/camera_hosts.js`.

### 5.1 Layout

1. **Status banner** (`#status`).
2. **Hosts** — one card per entry from `GET /nodes`, joined with
   `GET /stream-bindings` (grouped by `node_id`) for live per-sensor status:
   - **Local (`cam10`)**: badge `local · gateway`, always "ready". Sensor row
     (color/depth/ir1/ir2) with live status pill (`online`/`offline`/`stale`)
     and an activate toggle.
   - **Remote**: badge = `reachability` + `provision_state`. Renders only the
     steps the node still needs:
     - host key not pinned → **Confirm host key** (shows captured SHA256;
       operator pastes the out-of-band fingerprint → confirm).
     - pinned but not `ready` → **Provision** (prompts sudo password; polls
       `provision_state` reachable→probing→ready).
     - `ready` → sensor checkboxes + **Activate** (polls binding `status`).
3. **Add camera host** — IP + optional display name → `POST /nodes`. The new
   host appears in the list; the operator walks its steps. If the entered host
   is the local gateway (loopback / gateway LAN IP), the form does **not** post
   (the API would 400 on loopback); it scrolls to and highlights the existing
   local host card with a hint. (Honours "неважно local or remote" gracefully
   instead of erroring.)

### 5.2 Polling

After an async action, poll the relevant list (`/nodes` for provision_state,
`/stream-bindings` for sensor status) every 2s, bounded to ~60s, then stop and
show the last state. No websockets (matches the rest of the app).

### 5.3 Auth

`ConsoleLib.authFetch` (sessionStorage `X-Admin-Token`, prompt + 403 retry) —
identical to every other admin page. Mutating calls are rate-limited
server-side (5 rpm); the UI serialises actions and surfaces 429 as a friendly
"slow down" status rather than spamming.

## 6. Out of scope for v1 (explicit non-goals)

- **Deactivate / remove**: wired if cheap (local → `sensor_lifecycle.stop`;
  remote → `/stream-bindings/{id}/remove`), else a fast follow. Add is the ask.
- **No producer-mode merge, no allocator merge, no FDIR-domain merge** (§2).
- **No new auth surface** — reuses `require_admin` + the existing rate limiter.
- Bundle build / wheel packaging is unchanged (a provision pre-req, separate).

## 7. Testability

- **Backend**: pytest the new local branch of `activate_node_streams`
  (`node_id==cam10` → `sensor_lifecycle.initialize` called once per sensor,
  no transport built, `sudo_password` not required); `_local_serial()`
  resolution order (probe → allocations → sentinel); remote path regression
  (still builds transport, still rejects unconfirmed host key). All via the
  existing FastAPI `TestClient` + monkeypatched services.
- **Frontend**: `templates/tests/camera_hosts_tests.js` (Node `vm` sandbox,
  mocked `fetch`/`document`) — host-card rendering by state, local-IP detection
  in the add form, the activate→poll loop calling the right endpoint.

## 8. Files

- `app/routes/stream_bindings.py` — local branch in `activate_node_streams`;
  `_local_serial()` (or in a small service helper).
- `app/routes/templates.py` — `GET /camera_hosts.html` route.
- `app/services/sensor_lifecycle.py` — cross-process `_sensor_lock(serial,
  sensor)` flock around the `initialize`/`stop` start sequence (review C2).
- `app/services/device_registry.py` — cached `local_serial()`.
- `app/services/stream_binding_store.py` — `add_node_by_host` also rejects the
  gateway's own LAN IP (review L1, the `TODO(S10)`).
- `app/routes/templates.py` — `GET /camera_hosts.html` route (both nonces +
  `gateway_lan_ip` in context).
- `templates/camera_hosts.html`, `static/js/camera_hosts.js` (new).
- nav link from `console.html` / `admin_config.html` / `devices_dashboard.html`.
- `templates/tests/camera_hosts_tests.js`, `tests/test_stream_bindings_local_activate.py` (new).

## 9. Concurrency (review C2)

`sensor_lifecycle.initialize`/`stop` take **no lock** around the
mux + `rs-stream@{sensor}` start/stop + readiness check; the allocator flock is
released before those shell-outs. Three separate processes drive that
entrypoint — the admin route, the boot reconciler (`sensor-reconcile.service`),
and the local FDIR recovery adapter — so an in-process lock is insufficient.
Add a **per-`(serial,sensor)` flock** (`/run/lock/sensor-lifecycle-…lock`,
`try/finally`, bounded timeout → `LifecycleError` surfaced synchronously) at the
`sensor_lifecycle` chokepoint so it protects all three callers at once. Lock
order is sensor-flock (outer) → allocator-flock (inner), consistently, so no
deadlock. Idempotent `initialize` means a serialized second caller just
confirms-already-running and returns success.

## 10. Adversarial review — findings incorporated

| # | sev | finding | resolution |
|---|---|---|---|
| C1 | CRIT | async local loses errors; failed color renders `online` | local stays **synchronous**, returns per-sensor `results` (§4) |
| C2 | CRIT | unguarded concurrent encoder start vs reconciler | per-`(serial,sensor)` flock at chokepoint (§9) |
| H1 | HIGH | serial resolver: no cheap probe, swap-clobber, depth orphan | allocations-first; depth/ir refuse bare sentinel; 409 on mismatch (§4.1) |
| H2 | HIGH | local activation unaudited | emit `audit(...)` with terminal outcome |
| M1 | MED | 5 rpm vs per-sensor activate → 429 | one activate call carries **all** sensors |
| M2 | MED | `no_camera`/`failed`/unreachable/503 dead-ends | first-class card states + reasons; reachability ⟂ provision_state |
| M3 | MED | `_node_for_provision` 400/503 hides local branch | branch on `cam10` before that guard (§4) |
| L1 | LOW | browser can't know gateway LAN IP; `add_node_by_host` allows it | expose `gateway_lan_ip`; reject it server-side too |
| L2 | LOW | two CSP nonces, not one | template passes `style_nonce` + `script_nonce`; external JS |
| L3 | — | local bypass security | confirmed no new surface (admin-gated, no SSH) |

FDIR-cap (review area 1): **clean** — verified no path produces a cross-mode
binding; design keeps modes/windows/domains separate.

## 11. Operator console (P0, 2026-06-19) — beyond the onboarding wizard

The first cut was an onboarding wizard (add → confirm key → provision → activate →
status). Operating *several* cameras needs lifecycle + diagnostics + cleanup. P0
adds these without changing the onboarding flow or the FDIR cap.

**New endpoints** (all admin-gated + rate-limited + audited):

| verb | path | purpose |
|---|---|---|
| DELETE | `/nodes/{id}` | forget a host: destroy mountpoints → atomic `remove_node` (row+bindings+secret+key) → firewall reconcile drops stale ACCEPTs. `?deprovision=true` also best-effort stops the node's encoders first. Gateway-only is the MVP scope. |
| POST | `/nodes/{id}/maintenance` `{enabled}` | pause/resume FDIR for one node while servicing hardware |
| POST | `/stream-bindings/{id}/fdir` `{enabled}` | per-binding FDIR toggle (remote only) |
| POST | `/stream-bindings/{id}/restart` | restart one stream (remote: node-agent; local: encoder) |
| POST | `/stream-bindings/{id}/stop` | deliberate stop (local: `sensor_lifecycle.stop` → flips `desired_active`; remote: node-agent `/stop_stream`) |
| GET | `/stream-bindings?include_rtp_age=true` | adds `rtp_age_ms` (from `janus_summary`) per binding; default omits it (cheap) |

**Maintenance is FDIR-aware by construction.** `remote_stream_monitor.tick()`
already skips bindings where `not fdir.enabled`; it now *also* skips bindings whose
node is in `maintenance`. So touching a camera/USB/cable raises **no** false
recovery and **no** alert flood — the reviewer's hard requirement. `maintenance`
(node-wide, temporary) and `fdir.enabled` (per-binding, durable) stay orthogonal.

**Diagnostics surfaced on the node card** (no `journalctl` needed): `last_error`
(persisted by the provisioner on failure, cleared on success), `last_checked_at`
("last seen Ns ago", written by `/nodes/check`), `serial`, and per-stream
`rtp_age_ms`. **Token never surfaced** — only `host_key_pinned` (bool) and the
rotate action.

**Stop vs recovery** are distinct verbs on the node-agent: `/restart_stream`
(FDIR recovery) and `/stop_stream` (operator). A stop must be *deliberate and
no-auto-restart*: stopping a remote binding therefore **also disables FDIR for that
binding** (`set_fdir_enabled(False)`) — otherwise the monitor would see the now-stale
stream and restart it within a heartbeat, undoing the stop. The `fdir off` badge
makes this visible; the operator re-enables FDIR (or Restart) to resume
auto-recovery. (Found in adversarial review — the confirm-dialog nudge alone was
insufficient.)

### Still deferred (not P0)
Capability-based sensor list (vs hardcoded color/depth/ir1/ir2), bundle
version/sha/signing panel, fleet drift page (`/fleet/*` exists, no UI yet),
`deprovision=true` full node teardown (encoders stopped, but bundle/services left).
