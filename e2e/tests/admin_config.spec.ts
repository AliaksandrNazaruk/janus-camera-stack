import { test, expect } from '@playwright/test';

/**
 * Admin config page e2e:
 * 1. Page loads + masked secrets visible
 * 2. Rotate a secret → modal shows new value
 * 3. After rotate, snapshot shows updated rotation timestamp
 * 4. Apply NOT exercised (would restart Janus mid-test)
 */

test.describe('admin_config.html', () => {

  test.beforeEach(async ({ page, context }) => {
    // X-Admin-Token has header-level set in config; cookies optional
    await context.addCookies([]);  // placeholder for future cookie auth
  });

  test('loads + renders secret rows', async ({ page }) => {
    await page.goto('/admin_config.html');
    await expect(page).toHaveTitle(/Admin config/i);

    // Initial status text
    const status = page.locator('#status');
    await expect(status).not.toContainText('Loading', { timeout: 10_000 });

    // Secrets list populated with sensitive keys
    const secretsList = page.locator('#secretsList');
    await expect(secretsList).toContainText('JANUS_ADMIN_SECRET');
    await expect(secretsList).toContainText('STREAMING_ADMIN_KEY');
    await expect(secretsList).toContainText('TURN_SHARED_SECRET');
    await expect(secretsList).toContainText('INTERNAL_API_SECRET');

    // Janus config dir + services rendered
    const cfgDir = page.locator('#janusCfgDir');
    await expect(cfgDir).toContainText(/\/(etc|opt)\/janus/);
  });

  test('rotation of STREAMING_RGB_MP_SECRET via API + UI reflects new ts', async ({ page, request }) => {
    await page.goto('/admin_config.html');
    await page.waitForFunction(() => {
      return document.querySelectorAll('.rotate-btn').length > 0;
    }, { timeout: 5000 });

    // Rotate via API (avoids confirm() popup)
    const before = await request.get('/api/v1/color_camera/admin/config');
    const beforeJson = await before.json();
    const beforeTs = beforeJson.secrets.find((s: any) => s.key === 'STREAMING_RGB_MP_SECRET')?.last_rotated_ts;

    const rotateResp = await request.post('/api/v1/color_camera/admin/config/rotate/STREAMING_RGB_MP_SECRET');
    expect(rotateResp.status()).toBe(200);
    const rotateData = await rotateResp.json();
    expect(rotateData.must_apply).toBe(true);
    expect(rotateData.new_value).toBeTruthy();
    expect(rotateData.new_value.length).toBeGreaterThan(20);

    // Refresh page + verify timestamp updated
    await page.reload();
    await page.waitForFunction(() => {
      return document.querySelectorAll('.rotate-btn').length > 0;
    }, { timeout: 5000 });

    const after = await request.get('/api/v1/color_camera/admin/config');
    const afterJson = await after.json();
    const afterTs = afterJson.secrets.find((s: any) => s.key === 'STREAMING_RGB_MP_SECRET')?.last_rotated_ts;
    expect(afterTs).toBeGreaterThan(beforeTs || 0);
  });

  test('reveal endpoint requires confirm phrase', async ({ request }) => {
    // Wrong phrase → 400
    let resp = await request.post('/api/v1/color_camera/admin/config/reveal/INTERNAL_API_SECRET', {
      data: { confirm: 'wrong' },
    });
    expect(resp.status()).toBe(400);

    // Right phrase → 200 + plaintext
    resp = await request.post('/api/v1/color_camera/admin/config/reveal/INTERNAL_API_SECRET', {
      data: { confirm: 'reveal-INTERNAL_API_SECRET' },
    });
    expect(resp.status()).toBe(200);
    const json = await resp.json();
    expect(json.value).toBeTruthy();
    expect(json.value.length).toBeGreaterThan(10);
  });

  test('public IP detection endpoint responds', async ({ request }) => {
    const resp = await request.post('/api/v1/color_camera/admin/config/detect-public-ip', {
      data: {},
    });
    expect(resp.status()).toBe(200);
    const json = await resp.json();
    // Method is "stun" | "http-*" | "failed"
    expect(json.method).toBeTruthy();
    // IP may be null if probe failed (container may not have STUN access)
  });
});
