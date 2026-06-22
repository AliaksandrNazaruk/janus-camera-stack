"""Playwright shared fixtures for e2e browser tests.

Provides base_url + admin_token from environment, async browser context
with realistic viewport + permissions, and helpers for common waits.
"""
from __future__ import annotations

import os

import pytest
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


BASE_URL = os.environ.get("BASE_URL", "http://localhost:8201")
ADMIN_TOKEN = os.environ.get("CAM_ADMIN_TOKEN", "524bcff83b85a5c27dd510dca1eb1891")
DEVICE_SERIAL = os.environ.get("DEVICE_SERIAL", "141722072135")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def admin_token() -> str:
    return ADMIN_TOKEN


@pytest.fixture(scope="session")
def device_serial() -> str:
    return DEVICE_SERIAL


@pytest.fixture
async def browser() -> Browser:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=os.environ.get("HEADED") != "1",
            args=["--use-fake-ui-for-media-stream",  # bypass camera permission prompt
                  "--autoplay-policy=no-user-gesture-required"],
        )
        yield browser
        await browser.close()


@pytest.fixture
async def context(browser: Browser) -> BrowserContext:
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        permissions=["camera", "microphone"],
        ignore_https_errors=True,
    )
    # Inject admin token into sessionStorage on every page load
    await context.add_init_script(f"sessionStorage.setItem('camera_admin_token', '{ADMIN_TOKEN}')")
    yield context
    await context.close()


@pytest.fixture
async def page(context: BrowserContext) -> Page:
    page = await context.new_page()
    yield page
    await page.close()
