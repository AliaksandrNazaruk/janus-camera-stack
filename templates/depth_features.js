/**
 * ╔═══════════════════════════════════════════════════════════════╗
 * ║  CANONICAL FILE — DO NOT REPLACE / REGENERATE / AUTO-EDIT   ║
 * ║  This is the single source of truth for depth click overlay. ║
 * ║  Broken versions keep appearing during deploys — if you      ║
 * ║  need to change this, verify coordinate mapping first.       ║
 * ║  See: /memories/repo/DO_NOT_TOUCH_depth_features_js.md       ║
 * ╚═══════════════════════════════════════════════════════════════╝
 *
 * depth_features.js — Depth-click overlay for the depth-camera player.
 *
 * Injected into the depth_view.html page.  Adds:
 *   • crosshair cursor that follows the mouse over the <video>
 *   • click → HTTP GET /depth?x=…&y=… → depth value shown in HUD + badge
 *
 * All depth queries go through the local janus-camera-page `/depth` endpoint
 * which proxies to realsense_mux on port 8000.
 *
 * Coordinates sent to the server are in 0..100 range (percent of the
 * already-rotated video frame).  The CSS applies an additional 180° rotation,
 * so we compensate: frame_x = 100 - viewport_x, frame_y = 100 - viewport_y.
 *
 * The depth comes back in metres (or zero when unavailable).
 */
(function () {
  'use strict';

  // ── Config ──────────────────────────────────────────────────
  // Legacy HTTP path (used as fallback if textroom round-trip unavailable):
  const DEPTH_API = (document.body.dataset.depthEndpoint ||
                     (document.body.dataset.apiPrefix || '') + '/depth');
  // P0-SEC-001 (Phase 1): per-tab random session id ensures depth_result
  // SSE delivery routed only to this tab — not broadcast cross-session.
  // 16 bytes of crypto-randomness, base36 — short URL-safe token.
  const SESSION_ID = (window.crypto && window.crypto.getRandomValues)
    ? Array.from(window.crypto.getRandomValues(new Uint8Array(12)),
                 b => b.toString(36).padStart(2, '0')).join('')
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
  // Sprint X3.2 — async textroom round-trip:
  //   request: backChannel.publish('depth_query', {req_id, session_id, x, y})
  //   response: SSE /depth_events?session_id=X stream, matched by req_id
  const SSE_URL = (document.body.dataset.apiPrefix || '') + '/depth_events?session_id=' + encodeURIComponent(SESSION_ID);
  const QUERY_TIMEOUT_MS = 1500;

  // ── State ───────────────────────────────────────────────────
  let video = null;
  let sseSource = null;
  // Phase 1 fix: bound pendingQueries — if SSE silent and timeouts don't fire
  // (e.g., clock skew), Map could grow unbounded. Hard cap = oldest pruned.
  const _pendingQueries = new Map();   // req_id → {resolve, timer}
  const _PENDING_QUERIES_MAX = 64;
  let _reqCounter = 0;

  // ── DOM references (from depth_view.html) ───────────────────
  let depthHud = null;   // #depthHud  — persistent x/y/depth readout
  let depthToast = null; // #depthToast — fade-in toast on click

  // ── DOM injection ───────────────────────────────────────────

  function injectCSS() {
    const style = document.createElement('style');
    style.textContent = `
      /* Crosshair */
      #df-crosshair {
        position: fixed;
        width: 32px; height: 32px;
        pointer-events: none;
        z-index: 9000;
        display: none;
        transform: translate(-50%, -50%);
      }
      #df-crosshair::before, #df-crosshair::after {
        content: '';
        position: absolute;
        background: rgba(0, 255, 0, .8);
      }
      #df-crosshair::before { width: 2px; height: 100%; left: 50%; top: 0; transform: translateX(-50%); }
      #df-crosshair::after  { height: 2px; width: 100%; top: 50%; left: 0; transform: translateY(-50%); }

      /* Depth badge (shown on click) */
      #df-badge {
        position: fixed;
        z-index: 9001;
        pointer-events: none;
        display: none;
        background: rgba(0,0,0,.75);
        color: #0f0;
        font: bold 16px/1.2 monospace;
        padding: 4px 10px;
        border-radius: 6px;
        border: 1px solid rgba(0,255,0,.4);
        text-shadow: 0 0 4px rgba(0,255,0,.6);
        transform: translate(16px, -50%);
        white-space: nowrap;
      }

      /* Cursor on video */
      video { cursor: crosshair !important; }
    `;
    document.head.appendChild(style);
  }

  function injectDOM() {
    const ch = document.createElement('div');
    ch.id = 'df-crosshair';
    document.body.appendChild(ch);

    const badge = document.createElement('div');
    badge.id = 'df-badge';
    document.body.appendChild(badge);
  }

  // ── Click → sensor pixel mapping ────────────────────────────
  //
  // Two rotations stack visually on the depth frame:
  //   1) ffmpeg transpose in rs-{sensor}.tuning.env ROTATION (operator's
  //      camera_config UI control) — rotates FIFO bytes before encoding
  //   2) CSS rotate(var(--video-rotation)) on <video> (set by L4 template
  //      from rs-mux.env baseline) — rotates displayed element
  //
  // Mux's /depth samples RAW sensor (no rotation). To click-to-pixel correctly
  // we must inverse the combined visual rotation BEFORE sending coords:
  //
  //   total = (ffmpeg_deg + css_deg) mod 360
  //   sensor_coord = inverse_rotate(viewport_coord, total)
  //
  // Inverse formulas (CSS rotate(Ndeg) is CW visually):
  //   90 :  source (x, y) shown at viewport (100-y, x)  → inverse (y, 100-x)
  //   180:  source (x, y) shown at viewport (100-x, 100-y) → inverse (100-x, 100-y)
  //   270:  source (x, y) shown at viewport (y, 100-x) → inverse (100-y, x)
  //
  // ffmpeg rotation is poll'd live (see _refreshFrameRotation) so changing
  // camera_config UI propagates without full page reload — operator switches to
  // depth viewer tab → visibilitychange → fresh value → next click correct.

  function _readCssRotationDeg() {
    const m = /(-?\d+)\s*deg/i.exec(
      getComputedStyle(document.body).getPropertyValue('--video-rotation').trim());
    return m ? parseInt(m[1], 10) : 0;
  }

  async function _refreshFrameRotation() {
    const url = document.body.dataset.rotationPollUrl;
    if (!url) return;
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return;
      const { rotation } = await r.json();
      document.body.dataset.frameRotation = String(parseInt(rotation, 10) || 0);
    } catch (_) { /* network blip — keep last known value */ }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) _refreshFrameRotation();
  });
  // Low-frequency background poll in case the operator doesn't switch tabs.
  setInterval(_refreshFrameRotation, 5000);

  function viewportToFrame(xPct, yPct) {
    // Legacy hardcoded mode (kept for pre-X3 deployments that set it).
    if (document.body.dataset.depthCoordTransform === 'flip180') {
      return { x: 100 - xPct, y: 100 - yPct };
    }
    const cssRot = _readCssRotationDeg();
    const ffmpegRot = parseInt(document.body.dataset.frameRotation, 10) || 0;
    const total = ((ffmpegRot + cssRot) % 360 + 360) % 360;
    switch (total) {
      case 90:  return { x: yPct,         y: 100 - xPct };
      case 180: return { x: 100 - xPct,   y: 100 - yPct };
      case 270: return { x: 100 - yPct,   y: xPct       };
      default:  return { x: xPct,         y: yPct       };
    }
  }

  const _DF_VERSION = 'v2-180deg';

  // Aligned probe (P1-CV-001) semantically correct only when the displayed
  // frame == COLOR frame coords. depth_view.html shows RAW depth viz (depth
  // sensor coords), so aligned=true here gives parallax-shifted depth
  // on the wrong pixel. Phase 2 enable display-side reprojection (rs.align or
  // CPU); after that this toggle becomes meaningful in depth viewer.
  //
  // Currently: read only dataset.depthAligned (template default). depth viewer
  // forces "false" via data-depth-aligned; color viewer (when landed depth
  // probe) defaults to "true".
  const _DEPTH_ALIGNED = (document.body.dataset.depthAligned || 'true') !== 'false';

  // ── Depth HTTP query ────────────────────────────────────────

  // Legacy HTTP fetch — used as fallback if textroom/SSE unavailable.
  async function fetchDepthHTTP(frameX, frameY) {
    try {
      let url = `${DEPTH_API}?x=${frameX.toFixed(2)}&y=${frameY.toFixed(2)}`;
      if (_DEPTH_ALIGNED) url += '&aligned=true';
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return null;
      const data = await r.json();
      return data.depth;
    } catch (e) {
      console.warn('[depth_features] HTTP fetch error', e);
      return null;
    }
  }

  // Sprint X3.2 — textroom round-trip click query.
  function fetchDepthTextroom(frameX, frameY) {
    const bc = window.autonomousBackChannel;
    if (!bc || !bc.isReady || !bc.isReady()) return Promise.resolve(null);
    const reqId = `dq-${Date.now().toString(36)}-${_reqCounter++}`;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        _pendingQueries.delete(reqId);
        console.warn('[depth_features] textroom query timeout', reqId);
        resolve(null);
      }, QUERY_TIMEOUT_MS);
      // Bound — prune oldest if over limit (defensive against SSE silent failure).
      if (_pendingQueries.size >= _PENDING_QUERIES_MAX) {
        const oldestKey = _pendingQueries.keys().next().value;
        const oldest = _pendingQueries.get(oldestKey);
        if (oldest) { clearTimeout(oldest.timer); oldest.resolve(null); }
        _pendingQueries.delete(oldestKey);
      }
      _pendingQueries.set(reqId, { resolve, timer });
      try {
        bc.publish('depth_query', {
          req_id: reqId,
          session_id: SESSION_ID,  // P0-SEC-001 — server uses this to route SSE response to ONLY this tab
          x: Number(frameX.toFixed(2)),
          y: Number(frameY.toFixed(2)),
          aligned: _DEPTH_ALIGNED,  // P1-CV-001 — passthrough to mux /depth_query
          ts: Date.now(),
        });
      } catch (e) {
        clearTimeout(timer);
        _pendingQueries.delete(reqId);
        console.warn('[depth_features] textroom publish failed', e);
        resolve(null);
      }
    });
  }

  async function fetchDepth(frameX, frameY) {
    // Prefer textroom round-trip when the back-channel is available; fall back to HTTP.
    const bc = window.autonomousBackChannel;
    if (bc && bc.isReady && bc.isReady()) {
      const depth = await fetchDepthTextroom(frameX, frameY);
      if (depth !== null) return depth;
    }
    return fetchDepthHTTP(frameX, frameY);
  }

  function _initSSE() {
    if (sseSource) return;
    try {
      sseSource = new EventSource(SSE_URL);
      sseSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          const reqId = data && data.req_id;
          if (!reqId) return;
          const pending = _pendingQueries.get(reqId);
          if (!pending) return;
          clearTimeout(pending.timer);
          _pendingQueries.delete(reqId);
          pending.resolve(typeof data.depth === 'number' ? data.depth : null);
        } catch (err) {
          console.warn('[depth_features] SSE parse error', err);
        }
      };
      sseSource.onerror = (e) => {
        // EventSource auto-reconnects; just log.
        console.log('[depth_features] SSE state', sseSource.readyState);
      };
      console.log('[depth_features] SSE opened to', SSE_URL);
    } catch (e) {
      console.warn('[depth_features] SSE init failed — falling back to HTTP', e);
    }
  }

  function formatDepth(d) {
    if (d == null || d === 0) return '--';
    if (d > 100) return (d / 1000).toFixed(3) + ' m';
    return d.toFixed(3) + ' m';
  }

  // ── HUD update ──────────────────────────────────────────────

  function updateHud(frameX, frameY, depth) {
    if (depthHud) {
      depthHud.innerHTML =
        `x: ${frameX.toFixed(1)}%  y: ${frameY.toFixed(1)}%<br>depth: ${formatDepth(depth)}`;
    }
  }

  function showToast(frameX, frameY, depth) {
    if (!depthToast) return;
    depthToast.innerHTML =
      `<strong>${formatDepth(depth)}</strong> @ (${frameX.toFixed(1)}%, ${frameY.toFixed(1)}%)`;
    depthToast.style.display = 'block';
    depthToast.style.opacity = '1';
    clearTimeout(depthToast._timer);
    depthToast._timer = setTimeout(() => {
      depthToast.style.opacity = '0';
      setTimeout(() => { depthToast.style.display = 'none'; }, 250);
    }, 3000);
  }

  // ── Click-to-depth ──────────────────────────────────────────

  function onVideoClick(e) {
    const rect = video.getBoundingClientRect();
    const vpX = ((e.clientX - rect.left) / rect.width) * 100;
    const vpY = ((e.clientY - rect.top) / rect.height) * 100;

    const frame = viewportToFrame(vpX, vpY);

    // Show crosshair at click position
    const ch = document.getElementById('df-crosshair');
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
    ch.style.display = 'block';

    // Show badge
    const badge = document.getElementById('df-badge');
    badge.style.left = e.clientX + 'px';
    badge.style.top = e.clientY + 'px';
    badge.textContent = '…';
    badge.style.display = 'block';

    console.log(`[depth_features ${_DF_VERSION}] click viewport=(${vpX.toFixed(1)}, ${vpY.toFixed(1)}) → frame=(${frame.x.toFixed(1)}, ${frame.y.toFixed(1)})`);

    fetchDepth(frame.x, frame.y).then(d => {
      console.log(`[depth_features ${_DF_VERSION}] depth=${d} at frame(${frame.x.toFixed(1)}, ${frame.y.toFixed(1)})`);
      badge.textContent = formatDepth(d);
      updateHud(frame.x, frame.y, d);
      showToast(frame.x, frame.y, d);
    });
  }

  function onVideoMouseMove(e) {
    const ch = document.getElementById('df-crosshair');
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
    ch.style.display = 'block';
  }

  function onVideoMouseLeave() {
    document.getElementById('df-crosshair').style.display = 'none';
  }

  // ── Init ────────────────────────────────────────────────────

  function init() {
    video = document.getElementById('video');
    if (!video) {
      console.warn('[depth_features] no <video id="video"> found');
      return;
    }

    const ds = document.body.dataset;
    const hudSel = ds.depthHud || '#depthHud';
    const toastSel = ds.depthToast || '#depthToast';
    depthHud = document.querySelector(hudSel);
    depthToast = document.querySelector(toastSel);

    injectCSS();
    injectDOM();

    video.addEventListener('click', onVideoClick);
    video.addEventListener('mousemove', onVideoMouseMove);
    video.addEventListener('mouseleave', onVideoMouseLeave);

    // Open SSE for async textroom round-trip responses.
    _initSSE();

    console.log(
      `[depth_features] initialised ${_DF_VERSION}, DEPTH_API =`,
      DEPTH_API, 'SSE =', SSE_URL,
    );
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
