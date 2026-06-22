# Changelog

All notable changes to janus-camera-page documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versions: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Public release polish** (Sprint B7): LICENSE (Apache 2.0), NOTICE,
  CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, GitHub issue + PR templates,
  scoped CI workflow in `.github/workflows/`.
- **Cloud deployment configs** (Sprint B6): production Dockerfile,
  `docker-compose.prod.yml`, raw k8s manifests (`deploy/k8s/`),
  Helm chart (`deploy/helm/janus-camera-stack/`), Janus jcfg templates,
  `docs/DEPLOYMENT_CLOUD.md` covering three deploy paths.
- **Sensor type plugin SDK** (Sprint B5):
  `app/services/sensor_registry.py` exposes `register_sensor_type()`,
  plugin loader scans `/etc/robot/plugins.d/*.py` at startup,
  introspection endpoint `GET /api/v1/<cam>/sensor_types`,
  example plugin `app/plugins/example_usb_webcam.py`.
- **RTSP IP camera adapter** (Sprint B4):
  `host_infra/roles/encoder/files/rtp-rtsp.sh` + systemd template,
  registered in encoder-admin `UNIT_FAMILIES`.
- **Quickstart + dev compose** (Sprint B3): rewritten README as the project
  entry point, `docs/TUTORIAL_USB_WEBCAM.md` (12-step), `docker-compose.dev.yml`.
- **CameraAdapter abstraction** (Sprint B2): generic V4L2 adapter
  (`rtp-v4l2.sh`), `docs/ADAPTERS.md` taxonomy + how-to-add, fitness-test
  compliance fix for `INTERNAL_API_SECRET` via Settings class.
- **Robot wrapper file split** (Sprint B1): generic
  templates separated from `robot_overlay/` (joystick, gamepad, gripper);
  dispatch via `STACK_DEFAULT_JOYSTICK_MODE`.
- **Phase 2 hardening** (10/10 score push):
  internal HMAC auth + rate limiting on back-channel, ICE restart action
  before full session recreate, structured audit log
  (`app/services/audit_log.py`), Playwright e2e test suite,
  docs (ARCHITECTURE, OPERATOR_RUNBOOK, DEPLOYMENT).
- **Phase 1 stabilization**: SSE per-session isolation
  (P0-SEC-001), input/output FPS + jitter/RTT gauges, depth coord transform
  configurable, frontend client bug fixes (setTimeout leak, textroom adapter
  destroy, bounded query map), backend reliability (mux USB stall, polling
  readiness, age_ms/stale flag on depth responses).

### Changed
- `app/core/settings.py` now centralizes `internal_api_secret` (previously
  scattered `os.environ.get` in routes/).
- Templates: `templates/color_view.html` is now the generic player;
  robot-specific overlays moved to `templates/robot_overlay/`.

### Security
- SSE responses are now session-scoped — depth query response can no longer
  leak across browser tabs (Phase 1 P0-SEC-001).
- Internal HMAC authentication enforced on all back-channel admin paths.

## How to release

```bash
# 1. Update CHANGELOG.md: move [Unreleased] items under new version heading
# 2. Tag
git tag -a v0.1.0 -m "Initial public release"
git push origin v0.1.0
# 3. CI publishes container image to ghcr.io/YOUR_ORG/janus-camera-page:v0.1.0
```
