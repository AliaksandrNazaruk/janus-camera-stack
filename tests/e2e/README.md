# Browser E2E tests (Playwright)

CI-runnable browser automation tests covering critical user-facing scenarios.
Not run on the Pi5 (heavy install) — runs on a CI server or dev machine.

## Setup

```bash
pip install playwright pytest-playwright
playwright install chromium
```

## Run

```bash
# Against a local dev environment (assumes services already running):
BASE_URL=http://localhost:8201 pytest tests/e2e/ -v

# Against staging:
BASE_URL=https://staging.example.com pytest tests/e2e/ -v

# Single test:
pytest tests/e2e/test_stream_smoke.py::test_color_stream_reaches_playing -v
```

## Test scenarios

| File | Scenario | What it asserts |
|---|---|---|
| `test_stream_smoke.py` | Cold connect → PLAYING | Color stream reaches PLAYING state within 10s; video element receives frames |
| `test_depth_click.py` | Click → depth value displayed | Initialize depth, open viewer, click center → HUD shows valid metres |
| `test_reconnect_resilience.py` | Network blip → recovery | Throttle to offline 5sec, restore → stream recovers without a full reload |

## What's covered

- Browser→Janus→ICE→TURN→RTP→video tag full path
- Per-session SSE isolation (Phase 2 P0-SEC-001)
- ICE restart fallback path (Phase 2 P2-WEBRTC-002)
- Cold-start tolerance thresholds (Sprint X3.2 fix)

## What's NOT covered (yet)

- Multiple browsers same room (use multiple contexts)
- Mobile network simulation (DevTools throttle pattern)
- Audio handling (we run video-only)
- WebRTC stats accuracy (browser-specific)

## CI integration

GitHub Actions example in `.github/workflows/e2e.yml` (not deployed).
Run on PR to main branch against a staging environment.
