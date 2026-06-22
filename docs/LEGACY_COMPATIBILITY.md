# LEGACY_COMPATIBILITY — janus_camera_page

What *looks* legacy/dead but is **load-bearing**, with the external consumer or role that makes
it so. The recurring review mistake is "this looks old → delete it"; each entry here has been
checked and the answer is **keep**. Companion: [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md).

Last reconciled: 2026-06-20.

## 1. HTTP `/depth*` routes — compatibility surface, NOT dead

The **primary** click-to-depth path is now the Janus **textroom** round-trip (browser →
`textroom_relay` → `mux:8000/depth_query` → SSE `/depth_events`), which bypasses
`routes/depth.py` entirely. But the HTTP routes remain live:

| Route | Why it stays | Consumer |
|---|---|---|
| `GET /depth?x=&y=` | textroom **fallback** when the back-channel isn't ready | `templates/depth_features.js` (`fetchDepth` → `fetchDepthHTTP`) |
| `GET /depth/frame` | arm3d 3-D scene frame source | `xarm_service/services/depth_service.py:88`; `frontend_service` scene-helpers |
| `GET /depth/frame_color_overlay` | arm3d aligned RGBD | `frontend_service` scene-config |
| `GET /depth/color_frame` | currently no caller found | keep with the set until consumers are mapped |
| `GET /depth_map/load` (+`/api/v1`) | test-only today; documents the contract | — |

**Rule:** do not delete `/depth*` as "legacy". Retirement is gated on migrating
`xarm_service` + `frontend_service` arm3d off HTTP, then proving zero traffic — tracked
separately, not part of architecture cleanup. See
[design/ROUTE_PURITY_CLOSEOUT.md](design/ROUTE_PURITY_CLOSEOUT.md) Phase 6.

## 2. Root `realsense_mux.py` — depth-contract fixture, NOT a duplicate

`realsense_mux.py` (root) and `host_infra/roles/encoder/files/realsense-mux.py` are **two
different implementations with no shared API**, not two copies. The root file is the hardware-free
reference that backs the ratified `DEPTH_SEMANTIC_CONTRACT.md` test (`test_depth_contract.py`) +
`test_realsense_mux.py`; `app/` imports neither. The **deployed** mux is the encoder file
(`/usr/local/bin/realsense-mux`, `realsense-mux.service`). Full detail:
../SOURCE_OF_TRUTH.md §2. **Keep both** (distinct roles).

## 3. Deploy artifacts are NOT a duplicate FastAPI stack

`deploy/` (Helm chart + Janus config templates), `host_infra/` (encoder/janus roles, the
canonical mux + `camera-admin`), and `infrastructure/` (systemd units + overrides) are
**deployment artifacts and machine config** — not a second copy of `app/`. They look like "more
stack" but own a different concern (how it's installed/run on hardware). See
../PROJECT_FILE_MANIFEST.md.

## 4. `camera_bringup/` — separable L0 tooling, reached by CLI only

Self-contained L0 package (own `pyproject.toml`, tests, CONTRACT). `app/` **never imports it**
(architecture-fitness-enforced); L4 reaches it only via `sudo /usr/local/bin/camera-admin`
subprocess. Looks like "extra code in the repo" but is a clean, boundaried tool. Movable to
`tools/` later; not part of the L4 runtime.

## 5. `static/console/*` is generated, not hand-authored

Built from `design_system/` by `scripts/build_console.sh`. Edit the **source** in
`design_system/`, then rebuild — never edit `static/console/` directly. (Source-of-truth:
../SOURCE_OF_TRUTH.md §3.)

---

**If you are about to delete or "simplify" something that looks legacy:** check it against this
file and `SOURCE_OF_TRUTH.md` first. So far every legacy-looking item has had a live consumer.
