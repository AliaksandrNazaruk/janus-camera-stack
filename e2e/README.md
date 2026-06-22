# Playwright e2e tests

Browser-based end-to-end tests for `admin_config.html` and
`operator_dashboard.html` pages. Hits a running camera-page instance via
HTTP; expects admin token configured.

## Quick run (host node)

```bash
cd e2e
npm install
npx playwright install chromium

E2E_BASE_URL=http://127.0.0.1:18900 \
E2E_ADMIN_TOKEN=test-admin-token-123 \
  npx playwright test
```

## Quick run (Docker)

```bash
# Build runner image
docker build -f e2e/Dockerfile -t janus-e2e:latest e2e/

# Point at a running test container (e.g., janus-installer-test on host)
docker run --rm --network=host \
  -e E2E_BASE_URL=http://127.0.0.1:18900 \
  -e E2E_ADMIN_TOKEN=test-admin-token-123 \
  janus-e2e:latest
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `E2E_BASE_URL` | `http://127.0.0.1:18900` | URL of running camera-page |
| `E2E_ADMIN_TOKEN` | `test-admin-token-123` | Admin token (`CAM_ADMIN_TOKEN`) |

## What's covered

**admin_config.spec.ts:**
- Page loads + masked secrets visible
- Secret rotation via API + UI reflects new timestamp
- Reveal endpoint requires confirm phrase
- Public IP detection responds

**operator_dashboard.spec.ts:**
- Page loads + all 6 panels render
- Services panel lists known services
- Mountpoint CRUD: create via API → visible in UI → destroy → gone
- V4L2 + RealSense probe endpoints respond
- Audit log filter accepts query params
- Encoder instance status endpoint
- Prometheus `/metrics` returns expected gauges
- Mountpoint preview page renders + rejects invalid IDs

## What's NOT covered (intentionally)

- `Apply` (restarts Janus → kills mid-run state)
- `provision_stream` (writes to /etc/robot — needs cleanup teardown)
- Service restart buttons (would disrupt other tests)
- WebRTC video playback (heavy, requires fake-camera input)

These are exercised manually OR via separate integration suite.

## CI integration

Add to `.github/workflows/ci.yml`:

```yaml
e2e:
  needs: docker
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - run: docker compose -f docker-compose.prod.yml up -d
    - run: |
        cd e2e && npm ci && npx playwright install --with-deps chromium
        E2E_BASE_URL=http://localhost:8900 npx playwright test
    - if: failure()
      uses: actions/upload-artifact@v4
      with: { name: playwright-report, path: e2e/playwright-report }
```
