"""Smoke test: color stream reaches PLAYING state within reasonable time.

Validates entire WebRTC handshake path:
- Player loads → boot → loadRtcConfig
- Janus session create → handle attach → watch
- ICE gathering → connection → DTLS → SRTP → first frame
- Player state machine transitions IDLE → CONNECTING → PLAYING
- Video element receives frames
"""
from __future__ import annotations

import pytest
from playwright.async_api import Page

pytestmark = pytest.mark.asyncio


async def test_color_stream_reaches_playing(page: Page, base_url: str):
    """Cold open: navigate to color viewer, expect PLAYING within 10sec."""
    url = f"{base_url}/api/v1/color_camera/color_view.html"

    # Capture state transitions from player logs
    state_history: list[str] = []
    async def on_console(msg):
        text = msg.text
        if "STATE_TRANSITION" in text:
            state_history.append(text)
    page.on("console", on_console)

    await page.goto(url)

    # Wait for video element to be present
    video = await page.wait_for_selector("#video", timeout=5000)
    assert video, "video element not found"

    # Wait for PLAYING state (max 15sec)
    try:
        await page.wait_for_function(
            "() => document.getElementById('statusPill')?.textContent?.includes('PLAYING')",
            timeout=15000,
        )
    except Exception as e:
        pytest.fail(
            f"Stream did not reach PLAYING within 15s. State history: {state_history[-5:]}"
        )

    # Verify video element has track
    has_video = await page.evaluate("""() => {
        const v = document.getElementById('video');
        return v && v.srcObject && v.srcObject.getVideoTracks().length > 0;
    }""")
    assert has_video, "video element has no video track even after PLAYING"

    # Capture key player metrics
    metrics = await page.evaluate("""() => {
        const ctrl = window.autonomousPlayerController;
        if (!ctrl) return null;
        return {
            run_id: ctrl.cfg.run_id,
            state: ctrl.state,
            attempt: ctrl._reconnect?._attempt || 0,
        };
    }""")
    assert metrics, "player controller not exposed globally"
    assert metrics["attempt"] == 0, f"unexpected reconnect attempts on cold start: {metrics}"


async def test_video_dimensions_correct(page: Page, base_url: str):
    """Video element reports 640×480 (matches encoder config)."""
    await page.goto(f"{base_url}/api/v1/color_camera/color_view.html")
    await page.wait_for_function(
        "() => document.getElementById('statusPill')?.textContent?.includes('PLAYING')",
        timeout=15000,
    )
    # Give browser one frame to learn dimensions
    await page.wait_for_function(
        "() => document.getElementById('video').videoWidth > 0",
        timeout=5000,
    )
    dims = await page.evaluate("""() => {
        const v = document.getElementById('video');
        return { w: v.videoWidth, h: v.videoHeight };
    }""")
    # Allow either orientation (640×480 or 480×640 depending on rotation)
    assert {dims['w'], dims['h']} == {640, 480}, f"unexpected dimensions: {dims}"
