import { test, expect } from '@playwright/test';

/**
 * Operator dashboard e2e:
 * 1. Page loads + panels populated
 * 2. Live metrics widgets appear
 * 3. Mountpoint CRUD via API + verify in UI
 * 4. Hardware probe button works
 * 5. Audit log filter applies
 */

test.describe('operator_dashboard.html', () => {

  test('page loads + panels rendered', async ({ page }) => {
    await page.goto('/operator_dashboard.html');
    await expect(page).toHaveTitle(/Operator Dashboard/i);

    const status = page.locator('#status');
    await expect(status).not.toContainText('Loading', { timeout: 10_000 });

    // All major panels should be visible
    await expect(page.locator('h2:has-text("Services")')).toBeVisible();
    await expect(page.locator('h2:has-text("Janus mountpoints")')).toBeVisible();
    await expect(page.locator('h2:has-text("Encoder instances")')).toBeVisible();
    await expect(page.locator('h2:has-text("Hardware")')).toBeVisible();
    await expect(page.locator('h2:has-text("Audit log")')).toBeVisible();
    await expect(page.locator('h2:has-text("Live metrics")')).toBeVisible();

    // Stats bar populated
    await expect(page.locator('#statServices')).not.toContainText('…', { timeout: 5_000 });
  });

  test('services panel lists known services', async ({ page }) => {
    await page.goto('/operator_dashboard.html');
    const svcList = page.locator('#servicesList');
    await expect(svcList).toContainText('janus', { timeout: 10_000 });
    await expect(svcList).toContainText('janus-camera-page');
  });

  test('mountpoint CRUD: create → list → destroy', async ({ page, request }) => {
    const TEST_ID = 8001;
    const TEST_PORT = 5080;

    // Cleanup any leftover from previous run
    await request.delete(`/api/v1/color_camera/admin/mountpoints/${TEST_ID}`);

    // Create via API (UI form requires confirm() dialog handling)
    const created = await request.post('/api/v1/color_camera/admin/mountpoints', {
      data: {
        id: TEST_ID,
        description: 'e2e test mountpoint',
        rtp_port: TEST_PORT,
        codec: 'h264',
        payload_type: 96,
        is_private: false,
      },
    });
    expect(created.status()).toBe(200);
    const createdJson = await created.json();
    expect(createdJson.created).toBe(true);

    // Open dashboard + verify mountpoint visible
    await page.goto('/operator_dashboard.html');
    const mpList = page.locator('#mountpointsList');
    await expect(mpList).toContainText(`#${TEST_ID}`, { timeout: 10_000 });
    await expect(mpList).toContainText('e2e test mountpoint');

    // View link present
    const viewLink = page.locator(`a[href*="/preview/${TEST_ID}"]`);
    await expect(viewLink).toBeVisible();
    expect(await viewLink.getAttribute('target')).toBe('_blank');

    // Destroy via API
    const destroyed = await request.delete(`/api/v1/color_camera/admin/mountpoints/${TEST_ID}`);
    expect(destroyed.status()).toBe(200);
    const destroyedJson = await destroyed.json();
    expect(destroyedJson.destroyed).toBe(true);

    // Reload + verify gone
    await page.reload();
    await page.waitForFunction(() => {
      const txt = document.getElementById('mountpointsList')?.textContent || '';
      return !txt.includes('Loading') && !txt.includes('…');
    }, { timeout: 10_000 });
    const mpListAfter = await mpList.textContent();
    expect(mpListAfter).not.toContain(`#${TEST_ID}`);
  });

  test('V4L2 device probe endpoint', async ({ request }) => {
    const resp = await request.get('/api/v1/color_camera/admin/devices/v4l2');
    expect(resp.status()).toBe(200);
    const devices = await resp.json();
    expect(Array.isArray(devices)).toBe(true);
    // Container may have no V4L2 devices — accept empty list
  });

  test('RealSense probe endpoint responds', async ({ request }) => {
    const resp = await request.get('/api/v1/color_camera/admin/devices/realsense');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('available');
    expect(data).toHaveProperty('devices');
    // available may be false if pyrealsense2 not installed
  });

  test('audit log filter accepts query params', async ({ request }) => {
    const resp = await request.get('/api/v1/color_camera/admin/audit-log?limit=5&outcome=success');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('entries');
    expect(data).toHaveProperty('filters_applied');
    expect(data.filters_applied.outcome).toBe('success');
  });

  test('encoder instance status endpoint', async ({ request }) => {
    const resp = await request.get('/api/v1/color_camera/admin/encoders/status');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data)).toBe(true);
    // Container may have rtp-rgb@cam-rgb if installer performed a deploy
  });

  test('Prometheus /metrics returns expected gauges', async ({ request }) => {
    const resp = await request.get('/metrics');
    expect(resp.status()).toBe(200);
    const text = await resp.text();
    expect(text).toMatch(/camstack_video_age_ms/);
    expect(text).toMatch(/camstack_janus_reachable/);
  });

  test('mountpoint preview page renders', async ({ page }) => {
    const resp = await page.goto('/preview/1305');
    expect(resp?.status()).toBe(200);
    // color_view.html template
    await expect(page).toHaveTitle(/.+/);
  });

  test('preview rejects invalid mp_id', async ({ request }) => {
    const resp = await request.get('/preview/0');
    expect(resp.status()).toBe(400);
    const resp2 = await request.get('/preview/99999');
    expect(resp2.status()).toBe(400);
  });
});
