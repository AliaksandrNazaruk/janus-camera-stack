"""E2E depth click → metres value displayed.

Validates:
- Depth pipeline init via dashboard
- Per-session SSE isolation (P0-SEC-001)
- backChannel publish → mux query → SSE response → HUD update
- Coordinate transform passes through correctly
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import Page

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def depth_initialized(base_url: str, admin_token: str, device_serial: str):
    """Init depth pipeline before test, stop after."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        await client.post(
            f"/api/v1/cameras/{device_serial}/depth/initialize",
            headers={"X-Admin-Token": admin_token},
        )
        yield
        await client.post(
            f"/api/v1/cameras/{device_serial}/depth/stop",
            headers={"X-Admin-Token": admin_token},
        )


async def test_depth_click_updates_hud(
    page: Page, base_url: str, device_serial: str, depth_initialized,
):
    """Open depth viewer, click center of video, expect HUD shows depth value."""
    url = f"{base_url}/api/v1/cameras/{device_serial}/depth/viewer.html"
    await page.goto(url)

    # Wait for PLAYING
    await page.wait_for_function(
        "() => document.getElementById('statusPill')?.textContent?.includes('PLAYING')",
        timeout=20000,  # depth cold start tolerance (Sprint X3.2)
    )

    # Wait for SSE source open
    await page.wait_for_function(
        "() => document.body.dataset.depthEndpoint && window.EventSource",
        timeout=5000,
    )

    # Click center of video
    video = await page.query_selector("#video")
    box = await video.bounding_box()
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    await page.mouse.click(cx, cy)

    # Wait for HUD update (within 3sec of click)
    await page.wait_for_function(
        "() => document.getElementById('depthHud')?.textContent?.match(/depth: [\\\\d.]+ ?m/)",
        timeout=3000,
    )

    hud_text = await page.locator("#depthHud").text_content()
    assert "depth:" in hud_text, f"HUD doesn't show depth: {hud_text}"
    assert hud_text != "x: ---, y: ---\ndepth: ---", "HUD didn't update from initial"


async def test_depth_session_isolation(
    page: Page, context, base_url: str, device_serial: str, depth_initialized,
):
    """Two separate browser pages don't see each other's depth queries (P0-SEC-001)."""
    page_a = page
    page_b = await context.new_page()

    url = f"{base_url}/api/v1/cameras/{device_serial}/depth/viewer.html"
    await page_a.goto(url)
    await page_b.goto(url)

    # Both reach PLAYING
    for p in (page_a, page_b):
        await p.wait_for_function(
            "() => document.getElementById('statusPill')?.textContent?.includes('PLAYING')",
            timeout=20000,
        )

    # Get session IDs
    sid_a = await page_a.evaluate(
        "() => sessionStorage.getItem('camera_session_id') || (window.SESSION_ID || 'unknown')",
    )
    sid_b = await page_b.evaluate(
        "() => sessionStorage.getItem('camera_session_id') || (window.SESSION_ID || 'unknown')",
    )
    assert sid_a != sid_b, "browsers got same session_id — randomness broken"

    # Click in page A only
    video = await page_a.query_selector("#video")
    box = await video.bounding_box()
    await page_a.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    # Wait
    await page_a.wait_for_function(
        "() => document.getElementById('depthHud')?.textContent?.match(/depth: [\\\\d.]+ ?m/)",
        timeout=3000,
    )

    # Page B's HUD should remain default (no depth events received)
    hud_b = await page_b.locator("#depthHud").text_content()
    assert "---" in hud_b, f"Page B HUD updated after Page A click — session isolation broken: {hud_b}"

    await page_b.close()
