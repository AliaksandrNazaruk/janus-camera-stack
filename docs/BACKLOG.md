# Camera Stack — Backlog & Known Gaps

Consolidated live backlog. Replaces the completed March-2026 remediation tracker
(`plan.md`, 178/196 done) and reliability audit snapshot (`RELIABILITY_CHECKLIST.md`,
52 gaps resolved) — both were point-in-time; their completed history lives in git.

Only **still-open** items are kept here. Status key: `[ ]` todo · `[~]` partial · `[!]` blocked.

## Testing / CI
- `[~]` Test-suite cross-file isolation: full `pytest` run has ~16 failures that
  **pass in isolation** (recovery_ladder, layer_isolation, system_routes) — module-level
  global state leaks between test files (FDIR ladder state, system_mode, rate-limit
  buckets). Needs per-test reset fixtures or process isolation. FDIR code itself is correct.
- `[ ]` `CAM_TYPE=depth_camera pytest tests/` — verify versioned routes on depth profile
- `[ ]` Rate-limit bucket eviction test: 1000 reqs / multi-IP → window expiry → `_buckets == 0`
- `[ ]` `ruff check app/ tests/` → 0 errors
- `[ ]` e2e (Playwright) not yet wired into CI (`.github/workflows/`)

## Hardware / Deployment
- `[ ]` Color camera on USB 2.0 (480M) — should be USB 3.0 for higher res/fps (physical recable)
- `[ ]` No QoS / RTP traffic classification on either node (`qos-media.sh` exists, not auto-applied)
- `[ ]` coturn config not in repo (external VPS TURN); `nat_1_1_mapping` set at runtime via admin

## Refactors (low priority)
- `[ ]` Module-level singletons → IoC/AppState container (would also fix the test-isolation leak above)
- `[ ]` Split `system.py` god-module into health/relay/depth_map
- `[ ]` Contract tests: `realsense_mux.py` ↔ `realsense_mux_proxy.py`
- `[ ]` `requirements.txt` → lockfile for production builds (`lock-deps.sh` exists)
- `[ ]` `relay_get()` optional `asyncio.wait_for(timeout=3.0)` wrap
- `[ ]` Grafana "Rate Limiter State" panel

## Notes
- `depth.py` `_CAM_TYPE="depth_camera"` hardcoded — acceptable (module only loaded on depth nodes).
- Completed remediation (P0–P8 phases, March 2026) and the resolved reliability gaps are
  recorded in git history (search commits referencing `plan.md` / `RELIABILITY_CHECKLIST`).
