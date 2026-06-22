# Stream tuning in the operator console — rotation · resolution · fps · mode

Status: Phases A+B IMPLEMENTED (2026-06-19) — per-stream Configure (resolution/fps/
rotation/bitrate) for LOCAL and REMOTE streams in the console + WebRTC/STUN-TURN
editing. Remote tuning lands via node-agent `GET/POST /tuning` + the gateway
forwarder `…/stream-bindings/{id}/tuning` (verified live on .55: rotation written to
the node's rs-{sensor}.tuning.env + encoder restarted). Phase C (depth/ir remote
resolution/fps enumeration via node /modes, advanced encoder controls) pending.
The legacy `camera_config.html` let the operator set
**rotation/orientation**, **resolution**, **fps/mode**, and encoder quality, persist
them, and restart the encoder. The new design-system console (`/console.html`) does
not surface these yet. This plans where + how to add them.

## 1. What the mechanics are + where they already live (backend = mostly done)

Source of truth per (serial,sensor): **`/etc/robot/rs-{sensor}.tuning.env`**.

| Control | env key(s) | applied by | exists? |
|---|---|---|---|
| **Rotation / orientation** 0/90/180/270 | `ROTATION` | ffmpeg transpose pre-encode (+ a CSS `--video-rotation` baseline from rs-mux.env; `depth_features.js` inverse-rotates click-to-depth) | ✅ persisted |
| **Resolution** | `WIDTH`,`HEIGHT` | encoder capture profile | ✅ (color) |
| **FPS / mode** | `FPS` | encoder capture profile; valid combos from `/modes` (V4L2) + `/sensors` (RealSense) | ✅ (color) |
| **Bitrate / preset / tune / gop** | `BITRATE_KBPS`,`PRESET`,`TUNE`,`GOP` | x264 | ✅ |

Backend endpoints (admin-gated) — **already implemented**:
- `GET /cameras/{serial}/{sensor}/config` → current tuning (legacy `/api/v1/color_camera/config`).
- `POST /cameras/{serial}/{sensor}/config` → rewrite tuning.env **+ restart the encoder**.
- `GET /cameras/{serial}/{sensor}/modes` (V4L2) + `/sensors` (RealSense profiles) → selectable resolution/fps = the "mode".

> "Mode" (режим) = a *supported* (resolution, fps) capability combo. The form must
> populate Resolution + FPS from `/modes`/`/sensors` so an unsupported combo can't
> be chosen.

**Scope of what's tunable today:** color is fully runtime-tunable; depth/ir share
the `rs-{sensor}.tuning.env` ROTATION surface but resolution/fps use Initialize-time
defaults. **Remote nodes (.55): not wired** — `bootstrap.sh activate` only sets the
RTP port/target; node-side tuning is the gap (see §4).

## 2. Where in the new console (recommended placement)

Operator-console idiom = per-stream, action-driven. Add to the **Streams** screen
(and Command-Center live table) a per-row **"Configure" action** (gear icon) that
opens a **Stream-tuning drawer** (Class C — applying restarts the encoder):

```
Streams ▸ cam10:color ▸ [Open] [Restart] [Stop] [Configure ⚙]
  Configure ⚙ →  drawer "Tune cam10:color"
     Resolution  [640×480 ▾]     (options from /modes·/sensors)
     Frame rate  [30 ▾]
     Rotation    [ 0° | 90° | 180° | 270° ]   (segmented)
     Bitrate     [1800] kbps
     ▸ Advanced  preset [veryfast ▾]  tune [zerolatency ▾]  gop [..]
     Impact: encoder restarts · stream offline ~5–20s · viewers reconnect
     [Apply & restart]
```

- Pre-fill from `GET …/config`. Apply → `POST …/config` → drawer streams step
  progress (request → encoder restart → online) → refresh.
- Surface current tuning compactly on the StreamRow / Diagnostics▸Stream:
  `640×480 · 30fps · ↻90° · 1.8 Mbps`.
- **Rotation** is the most-used → also offer it as a quick inline control (rotate
  ⟳ button cycling 0→90→180→270) that does the same `POST …/config` (rotation only
  = arguably Class B, but it still restarts the encoder, so treat as C with a
  lighter impact line).

### Implementation constraint
The DS data components (StreamRow, etc.) are **pre-compiled into `_ds_bundle.js`**
(no local bundler). So the new "Configure" affordance + the tuning form are added
in the **screens layer** (`screens.jsx` StreamsTable wrapper + a new tuning
dialog/drawer built from ActionButton/inputs like the Wizard), not by editing the
compiled components.

## 3. View-model + API additions (small)

- Add per-stream `tuning` to `/api/v1/ui/fleet` (or a lazy `GET /api/v1/ui/stream/{binding}/config` that proxies `…/config` + `…/modes`) so the form pre-fills and validates without N round-trips: `{width,height,fps,rotation,bitrate,preset,tune,gop, modes:[{w,h,fps...}]}`.
- Reuse the existing **runtime-config validate→apply** two-step (`runtime_config_validator`) where it overlaps (resolution/fps/bitrate already in `StreamRuntimeConfig`), so tuning changes get impact-classed + revisioned like the rest.

## 4. Remote nodes (.55) — the real new work

Local reuses existing endpoints; remote needs node-side plumbing:
1. **node-agent**: new `POST /set_tuning?sensor=` (token-gated) → write
   `rs-{sensor}.tuning.env` on the node + `systemctl restart rs-stream@{sensor}`.
2. **bootstrap.sh**: extend `activate` (or a new `tune` verb) to accept
   `--resolution/--fps/--rotation/--bitrate` and persist them into the node's
   tuning.env (argv-only, validated — same hardening as the existing verbs).
3. **gateway**: a unified `POST /api/v1/admin/stream-bindings/{id}/tuning` that, by
   `mode`, either calls the local `…/config` (LOCAL_PRODUCER) or forwards to the
   node-agent / re-runs bootstrap tune (REMOTE_PRODUCER). One verb, two backends —
   mirrors how restart/stop already split local vs remote.

## 5. Staging

- **Phase A (local, fast):** Configure drawer + rotation quick-control on the
  Streams screen, wired to the existing `…/config` + `…/modes`; view-model tuning
  fields. Covers cam10 color (resolution/fps/rotation/bitrate) + depth/ir rotation.
- **Phase B (remote):** node-agent `/set_tuning` + bootstrap `tune` verb + the
  gateway `…/tuning` forwarder → the same drawer works for .55.
- **Phase C (polish):** depth/ir resolution/fps if the SDK exposes a runtime
  surface; advanced encoder controls; show tuning on StreamRow + Diagnostics.

## 6. Safety notes
- Applying restarts the encoder → **Class C** (confirm + impact "~5–20s offline").
- Only `/modes`-supported combos selectable (no arbitrary WIDTH/HEIGHT/FPS).
- Rotation has TWO layers (ffmpeg ROTATION + CSS baseline) and depth click-to-pixel
  inverse-rotates — changing ROTATION must keep `depth_features.js` correct (it
  already polls ffmpeg rotation live; verify after wiring).
- All tuning writes are admin-gated + audited (as `…/config` already is).
