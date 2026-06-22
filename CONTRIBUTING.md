# Contributing to janus-camera-page

Thanks for your interest. This document explains how to develop, test, and
submit changes.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md) v2.1.
By participating you agree to abide by its terms.

## Getting started

### Prerequisites
- Python 3.12+ (3.10/3.11 may work for L4 standalone, monorepo CI uses 3.12)
- ffmpeg 5.x (only on encoder nodes — L4-only dev needs no ffmpeg)
- Optional: Docker, k3s/minikube (for full-stack integration testing)

### Local dev (L4 only)
```bash
git clone https://github.com/YOUR_ORG/janus-camera-page.git
cd janus-camera-page
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, pytest, etc.

# Point at any reachable Janus instance (community demo OR docker)
export JANUS_API_URL=http://localhost:8088/janus
export JANUS_WS_URL=ws://localhost:8188

uvicorn main:app --reload --port 8900
# → http://localhost:8900/color_camera
```

### Full stack via Docker (no native deps needed)
See [docs/INSTALL.md](docs/INSTALL.md). The
`docker-compose.dev.yml` brings up coturn + Prometheus + Grafana and
proxies to your host's Janus (assumed running on default ports).

## Project layout

- `app/` — FastAPI L4 (routes, services, models)
- `static/` — JS client (Janus WebRTC adapter, BackChannel SDK)
- `templates/` — Jinja2 HTML (generic player + robot_overlay/)
- `tests/` — pytest suite (architecture fitness, unit, integration)
- `deploy/` — k8s manifests + Helm chart + Janus configs
- `docs/` — architecture, runbook, deployment, adapters, tutorials
- `host_infra/` — Ansible roles (deploy-time, not runtime)

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layer model (L0-L4).

## Development workflow

### Branch naming
- `feat/<short-desc>` — new features
- `fix/<short-desc>` — bug fixes
- `docs/<short-desc>` — doc-only changes
- `refactor/<short-desc>` — internal restructuring without functional change
- `test/<short-desc>` — test additions/fixes

### Commits
Format: `<type>(<scope>): <subject>` (Conventional Commits).

Examples:
- `feat(L4): add SSE per-session isolation`
- `fix(L0): rtp-v4l2 stall watchdog kills stale ffmpeg`
- `docs(deploy): clarify nat_1_1_mapping requirement`

Body:
- Explain WHY, not WHAT (code shows what).
- Reference issues: `Closes #123`, `Fixes #456`
- For non-trivial commits include a verification line:
  ```
  Verified: 36/36 tests pass, production stream live throughout deploy
  ```

### Tests
Required before PR:
```bash
# Architecture fitness (boundary enforcement)
pytest tests/test_architecture_fitness.py

# Full suite (Linux, requires v4l2-ctl in PATH for some adapter tests)
pytest tests/ -m "not hardware and not simulator"

# Lint
ruff check app/
```

Coverage target: 70% on `app/`. CI enforces this on PRs.

### When changing the contract
- L0/L1/L2/L3/L4 boundaries — update `tests/test_architecture_fitness.py`
- Camera adapter contract — update `docs/ADAPTERS.md`
- Sensor type fields — update `docs/ADAPTERS.md` "Plugin format"
- HTTP API surface — update OpenAPI inline docstrings (auto-rendered)

## Pull request process

1. Fork → branch from `main`
2. Make changes, run tests, lint
3. Push branch to your fork
4. Open PR against `main` with the [PR template](.github/PULL_REQUEST_TEMPLATE.md) filled
5. Wait for CI to pass
6. Maintainer reviews, may request changes
7. Squash + merge once approved

### What gets merged faster
- Clear problem statement in PR body
- Tests covering the change (regression-proof)
- Doc updates if user-facing
- Small, focused PRs (split large work into stages)

### What slows merges
- No tests
- Adding deps without discussion
- Mixing unrelated changes
- Breaking existing tests

## Adding a sensor type

Two paths:

### Path A: plugin (no PR needed)
Drop a `.py` file in `/etc/robot/plugins.d/` calling
`register_sensor_type()`. See [docs/ADAPTERS.md](docs/ADAPTERS.md)
"Sensor type plugins" section.

### Path B: upstream contribution
For sensors broadly useful (thermal, lidar, fisheye), submit a PR
adding to the `app/services/sensor_registry.py` built-ins block. Include:
- SensorType registration
- Adapter script in `host_infra/roles/encoder/files/`
- systemd unit template
- Tutorial doc in `docs/TUTORIAL_<sensor>.md`
- Tests for the adapter script (parse env vars, build ffmpeg cmdline)

## Adding a camera adapter

If your hardware needs a new transport (not V4L2 / RTSP / RealSense):

1. Read [docs/ADAPTERS.md](docs/ADAPTERS.md) "Adapter Contract"
2. Add `rtp-<family>.sh` to `host_infra/roles/encoder/files/`
3. Add systemd unit `rtp-<family>@.service`
4. Register family in `encoder-admin.py` `UNIT_FAMILIES`
5. Add example tuning + contract env files
6. Document in the ADAPTERS.md "Existing implementations" section

## Reporting bugs

Use the bug report template. Include:
- Steps to reproduce
- Expected vs actual
- Stack version (commit hash or release tag)
- Browser + OS (for client-side bugs)
- Janus version + jcfg snippet (for streaming issues)
- Logs from `journalctl -u janus-camera-page` (last 50 lines)

## Security

DO NOT open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md) for the private reporting channel.

## License

By contributing, you agree your contributions will be licensed under the
[Apache License 2.0](LICENSE).
