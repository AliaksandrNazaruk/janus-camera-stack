# STRICT_ARCHITECTURE_HARDENING — next maturity tier (post D1/D2/D3)

A strict Clean/Hexagonal architecture audit scored the service **~7/10**: no longer chaotic
(D1/D2/D3 closed the fat route, god-store, and FastAPI-everywhere leak) but still **mid-migration** —
broad `services/`, `application/` depending on concrete services rather than ports, a few residual
fat routes / god-services, and architecture guards that are too narrow. This campaign raises strict
purity, **guardrails-first**. Companions: [../ARCHITECTURE_CURRENT.md](../ARCHITECTURE_CURRENT.md),
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).

This is **purely architecture maturity** — the release artifact, the test suite, and the runtime
viewer-auth path are already clean (separate concerns; do not conflate).

## Audit findings accepted (real debt — NOT regressions of the closed D1/D2/D3)
- **A-01** `routes/device_camera.py` still a fat route (imports sibling routes; rendering/orchestration).
- **A-02** `routes/stream_bindings.py` residual provisioning/SSH glue (`_node_for_provision`,
  `_transport_for`, `NODE_BUNDLE_TAR`, `SSHTransport`, `capture_host_key`, provision/rotate/activate).
- **A-04** `services/sensor_lifecycle.py` god service (mixed activation/stop/config/readiness).
- **A-05** architecture tests too narrow.
- **A-06** `services/` is a broad dumping ground.
- **A-09** corruption must fail closed — already DONE for the store (H-02) and the op-journal (H3);
  remaining: verify no other silent-empty loader exists.
- **A-10** blocking ops still in the request path (the D8 sync `/janus/restart`, etc.).

## Phase-1 recon — which guard passes today (verified 2026-06-21)
| Guard | Today | Note |
|---|---|---|
| no file writes in routes (G2) | ✅ 0 violations | **lock now**, no allowlist |
| no subprocess/systemctl in routes (G3) | ✅ enforced | already guard #8 (`test_routes_have_no_subprocess_systemctl_httpx`, unconditional) |
| no route→route imports (G1) | ❌ 3, all `device_camera.py` | **ratchet allowlist** → empties in Phase 2 |
| no FastAPI in `application/` (G4) | ⚠️ 1 file (`depth_mux_proxy.py`) | **absolute once Phase 5 moves it** — `application/` is otherwise already FastAPI-free |
| no os.getenv outside settings (G5) | ❌ 72 sites (many legit) | settings-centralization effort; routes-subset already guard #5 |

## Decisions (gated 2026-06-21)
- **Ratchet with shrinking allowlists.** Add the guards that pass today; add the in-flight ones
  (G1, and G4 via Phase 5) as regression-locks with a *documented* allowlist of the known debt that
  MUST shrink to zero in its phase. **Never grow an allowlist** — fix the boundary instead.
- **Phase 5 early.** Move `depth_mux_proxy` out of `application/` first — it is small, single-file,
  and makes the FastAPI-free-`application` guard **absolute (no allowlist)** immediately. Highest
  leverage for least effort.

## Phases (reordered)
1. **P1 — guards (ratchet).** G2 lock-now; G1 with `{device_camera.py}` allowlist. *(this commit)*
2. **P5-early — `depth_mux_proxy`.** `app/application/depth_mux_proxy.py` → `app/services/proxy/`;
   split the D3 allowlist so `application/**` has NO FastAPI entry; tighten guard #9.
3. **P2 — `device_camera.py`** → `application/device_camera/*` + `services/sensor_tuning_env` +
   encoder-admin adapter. Empties the G1 allowlist. Route = auth + parse + call use-case + map + return.
4. **P3 — `stream_bindings.py` residual provisioning glue** → use-case + transport/bundle adapter factory.
5. **P4 — `sensor_lifecycle.py`** god-service split (activation / stop / config-store / encoder-admin
   port / mountpoint port / readiness probe).
6. **(separate, lower) G5 — settings centralization** (72 `os.getenv` sites → settings/config adapters).
   Also covers **import-time config reads / side-effects**: module-level `get_settings()` in Jinja
   loaders (`routes/templates.py`, `routes/device_camera.py:54`) and the `TURN_PASS/TURN_SHARED_SECRET`
   warning that fires at import (seen during a Phase-5 import sanity check) — these read config at
   import instead of call time, which makes tests order-sensitive. Make them lazy/call-time (the same
   fix D4 applied to the admin token). Tracked debt; NOT addressed during Phase 2.

## Red lines
No rewrite. No DDD-for-DDD. Keep `realsense_mux.py` (SOURCE_OF_TRUTH fixture). Allowlists only
shrink. No new features in `device_camera.py` / `sensor_lifecycle.py` while they're being thinned.
Every phase: recon → behavior-preserving move → tests re-pointed with identical assertions → full
non-e2e suite green → one gated commit.

## Phase 0 — test gate (added per the 2nd audit, 2026-06-21)

A second strict audit flagged CI/dev reproducibility as a precondition. Recon findings:
- **Local gate reproduces green** — `pytest tests/ --ignore=tests/e2e -p no:warnings -q` passes.
- **CI drift (fixed):** `.github/workflows/ci.yml` set the jcp test job's `requirements: requirements-dev.txt`
  (the **root** file) instead of `janus_camera_page/requirements-dev.txt`. It worked only because root-dev
  is a superset (has `respx`); it would silently break if jcp's dev deps diverged. Fixed to the full path
  (matches `igus_service`'s entry + the coverage job). Note: `xarm_service` (ci.yml) has the same latent
  bug — out of scope here, left for its owner.
- **Host wart (NOT a code fix):** `pip check` fails locally — `aiortc`/`pycares` in `/usr/lib/python3/dist-packages`
  (apt-installed for the gateway's WebRTC runtime, `Required-by:` empty) miss `cffi`/`google-crc32c`. Neither is
  a jcp requirement; CI runs in a fresh venv and never sees them. Host-maintenance only: `pip install cffi
  google-crc32c` (or `apt install python3-cffi python3-google-crc32c`) on the box if `pip check` cleanliness is wanted.

## CLOSEOUT — cycle CLOSED 2026-06-21 (HEAD d6c61d6)

The strict-architecture hardening cycle is **closed**. All accepted audit findings landed
behavior-preserving (recon → char tests → minimal move → guard → full non-e2e suite green), one gated
commit per sub-step. Final review artifact: `_archive/janus_camera_page_release_20260621_140152.tar.gz`
(sha256 `93cc10aa…`, 871 entries, HEAD `d6c61d6`).

Landed (in order):
- **Phase 0** CI test-gate drift fixed; **Phase 1** ratchet guards (no route writes / no route→route);
  **Phase 5-early** `depth_mux_proxy` → services (application/** FastAPI-free, no allowlist).
- **Phase 2A/2B (A-01)** `device_camera.py` thinned + fully decoupled; G1 route→route guard unconditional.
- **Guards #14/#15** systemctl-mutation chokepoint + no-new-public-URL.
- **Phase 3 (A-02)** `stream_bindings.py` provisioning glue → `node_transport` adapter + use-cases; guard #16.
- **core/events (audit #5)** `@app.on_event` → lifespan + task registry (loop-leak fixed); guard #17.
- **Phase 4 (A-04)** `sensor_lifecycle.py` → package behind an unchanged 28-caller facade; facade-contract char test.
- **P1 (service-control boundary)** — the headline closure: NO raw `sudo /bin/systemctl` in app/** anymore.
  All privileged service control goes `services/service_control.py` + recovery_executor → the scoped
  `service-admin` CLI (NOPASSWD sudoers scoped to one binary + an internal unit allowlist). Guard #14
  allowlist + boundary_fitness `_APPROVED_LEAKS` both **EMPTIED** (unconditional). **Host-side confirmed:**
  legacy broad sudoers removed; only scoped `*-admin` entries remain (`visudo -c` OK). See
  `SERVICE_CONTROL_BOUNDARY.md`.

**17 architecture fitness guards** green. The service is no longer "mid-migration" — the route/application/
services layering holds, the FastAPI-free application layer is absolute, and the operational authority
boundary is closed in both code and host config.

### Backlog (NOT blockers — open later, each its own recon-first gated cycle)
- **G5** settings centralization (72 `os.getenv` + import-time `get_settings()`/TURN-warning side-effects → lazy).
- **D8** operationize the blocking sync `/janus/restart` through the durable runner (like provision/rotate).
- **D6** `admin_config` service-import tidy.
- **De-dup** the two parallel `encoder_admin.py` (`services/encoder_admin.py` invoke/discover_units vs
  `services/sensor_lifecycle/encoder_admin.py` `_encoder_action`) — behavior-touching, separate cycle.
- **Latent:** `xarm_service` ci.yml `requirements-dev` drift (its owner); recovery_executor's pre-existing
  unused `import subprocess` (HEAD baseline).
