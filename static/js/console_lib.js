// console_lib.js — shared helpers for operator console pages.
// Pure browser globals, no module system — loaded via <script src=…>.
//
// Public API (exposed on window.ConsoleLib):
//   auth:      getToken, clearToken, authFetch
//   format:    fmtMs, fmtFps, fmtBytes, fmtAgo
//   dom:       $, setStatus, renderSparkline
//   nav:       navigate, parseHash, onRouteChange
//
// Backward-compat shim: each consumer page declares its own setStatus()
// pointing to a local element if it has multiple status indicators.

(function () {
  'use strict';

  // ─ Auth: sessionStorage-backed X-Admin-Token with 403 retry ──────────
  const TOKEN_KEY = 'camera_admin_token';

  function getToken(forcePrompt) {
    let t = forcePrompt ? null : sessionStorage.getItem(TOKEN_KEY);
    if (!t) {
      t = prompt('X-Admin-Token (CAM_ADMIN_TOKEN):') || '';
      t = t.trim();
      if (t) sessionStorage.setItem(TOKEN_KEY, t);
    }
    return t;
  }

  function clearToken() { sessionStorage.removeItem(TOKEN_KEY); }

  /** Fetch with automatic X-Admin-Token retry on 403. */
  async function authFetch(url, init) {
    const opts = Object.assign({ credentials: 'include' }, init || {});
    // Normalize headers to a fresh object: a caller passing `headers: undefined`
    // (e.g. a body-less POST) would otherwise overwrite the default and make
    // opts.headers undefined → "Cannot set properties of undefined". The cookie
    // session still authenticates such requests, but this must not throw first.
    opts.headers = Object.assign({}, opts.headers || {});
    let token = getToken(false);
    if (token) opts.headers['X-Admin-Token'] = token;
    let r = await fetch(url, opts);
    if (r.status === 403) {
      clearToken();
      token = getToken(true);
      if (!token) return r;
      opts.headers['X-Admin-Token'] = token;
      r = await fetch(url, opts);
    }
    return r;
  }

  // ─ Formatters ─────────────────────────────────────────────────────
  function fmtMs(v) { return Number.isFinite(v) ? v.toFixed(0) + 'ms' : '—'; }
  function fmtFps(v) { return Number.isFinite(v) ? v.toFixed(1) : '—'; }

  function fmtBytes(n) {
    if (!Number.isFinite(n)) return '—';
    if (n < 1024) return n + 'B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'KB';
    return (n / (1024 * 1024)).toFixed(2) + 'MB';
  }

  function fmtAgo(ts) {
    const ago = Date.now() / 1000 - ts;
    if (ago < 60) return Math.round(ago) + 's ago';
    if (ago < 3600) return Math.round(ago / 60) + 'm ago';
    if (ago < 86400) return (ago / 3600).toFixed(1) + 'h ago';
    return (ago / 86400).toFixed(1) + 'd ago';
  }

  // ─ DOM helpers ────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  /** Generic status banner — caller passes element id (default 'status'). */
  function setStatus(msg, cls, id) {
    const el = $(id || 'status');
    if (!el) return;
    el.textContent = msg;
    el.className = cls || 'info';
  }

  /** Render SVG sparkline. Resilient to single value / nan filtering. */
  function renderSparkline(svgEl, values, opts) {
    if (typeof svgEl === 'string') svgEl = $(svgEl);
    if (!svgEl) return;
    opts = opts || {};
    while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);
    const filtered = values.filter((v) => Number.isFinite(v));
    if (filtered.length < 2) return;

    const w = svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width || 200;
    const h = svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.height || 50;
    const min = Math.min(...filtered);
    const max = Math.max(...filtered);
    const range = max - min || 1;
    const step = w / (filtered.length - 1);

    const points = filtered.map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');

    const color = opts.color || '#2563eb';
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const polyline = document.createElementNS(SVG_NS, 'polyline');
    polyline.setAttribute('points', points);
    polyline.setAttribute('fill', 'none');
    polyline.setAttribute('stroke', color);
    polyline.setAttribute('stroke-width', '1.5');
    polyline.setAttribute('vector-effect', 'non-scaling-stroke');
    svgEl.appendChild(polyline);

    // Last-point dot
    const lastX = (filtered.length - 1) * step;
    const lastY = h - ((filtered[filtered.length - 1] - min) / range) * h;
    const dot = document.createElementNS(SVG_NS, 'circle');
    dot.setAttribute('cx', lastX.toFixed(1));
    dot.setAttribute('cy', lastY.toFixed(1));
    dot.setAttribute('r', '2');
    dot.setAttribute('fill', color);
    svgEl.appendChild(dot);
  }

  // ─ Hash-router (for SPA shell) ────────────────────────────────────
  /**
   * parseHash() → { path, query }
   *   '#/streams?filter=on' → { path: '/streams', query: {filter: 'on'} }
   *   ''                    → { path: '', query: {} }
   */
  function parseHash() {
    const raw = (location.hash || '').slice(1);
    const [path, qs] = raw.split('?');
    const query = {};
    if (qs) {
      qs.split('&').forEach((kv) => {
        const [k, v] = kv.split('=');
        if (k) query[decodeURIComponent(k)] = v ? decodeURIComponent(v) : '';
      });
    }
    return { path: path || '', query };
  }

  /** navigate(path, query?) — programmatic route change.
   *  Sets location.hash, which fires hashchange → registered callbacks. */
  function navigate(path, query) {
    let hash = '#' + path;
    if (query && Object.keys(query).length) {
      const qs = Object.keys(query)
        .map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(query[k]))
        .join('&');
      hash += '?' + qs;
    }
    if (location.hash !== hash) location.hash = hash;
    else _fireRouteChange();  // same hash → manually fire
  }

  const _routeListeners = [];
  function onRouteChange(cb) { _routeListeners.push(cb); }
  function _fireRouteChange() {
    const parsed = parseHash();
    _routeListeners.forEach((cb) => {
      try { cb(parsed); } catch (e) { console.error('route handler error:', e); }
    });
  }
  window.addEventListener('hashchange', _fireRouteChange);

  // ─ Public namespace ──────────────────────────────────────────────
  window.ConsoleLib = {
    getToken, clearToken, authFetch,
    fmtMs, fmtFps, fmtBytes, fmtAgo,
    $, setStatus, renderSparkline,
    parseHash, navigate, onRouteChange,
  };
})();
