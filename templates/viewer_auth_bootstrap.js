/* P0-SEC-001 viewer auth bootstrap.
 *
 * Loads BEFORE any player/depth_features script. Establishes the global token
 * exposure + transport injection so existing JS keeps working unmodified.
 *
 * Token discovery (first-hit wins):
 *   1. URL ?token=... — first-time deep-link. Persisted to sessionStorage so
 *      subsequent reloads in same tab keep auth.
 *   2. sessionStorage — set by step 1 of an earlier load.
 *   3. window.__viewerToken — explicitly assigned by host environment (tests,
 *      iframe parent passing credentials in postMessage).
 *
 * Transport injection:
 *   - fetch() — wrapped so every same-origin request gains 'X-Viewer-Token'.
 *      Cross-origin requests are NOT touched (safer default, avoids leaking
 *      credential to third-party CDNs).
 *   - EventSource — wrapped so that constructor URLs gain ?token= when same-origin.
 *      EventSource cannot set custom headers per WHATWG, hence query-param path.
 *   - WebSocket — wrapped similarly: same-origin WSS/WS gains ?token=.
 *
 * Dev mode: gate is off when token is empty string — wrapped methods pass-through.
 * Production deployments distribute the token out-of-band (operator URL link).
 *
 * Idempotent: re-running the script (e.g. via inline bundling) is a no-op.
 */
(function(){
  'use strict';
  if (window.__VIEWER_AUTH_BOOTSTRAPPED) return;
  window.__VIEWER_AUTH_BOOTSTRAPPED = true;

  const STORAGE_KEY = 'viewerToken';
  const HEADER_NAME = 'X-Viewer-Token';

  function discoverToken() {
    // 1. URL query param — strip after consume so it doesn't linger in history.
    try {
      const url = new URL(window.location.href);
      const fromUrl = url.searchParams.get('token');
      if (fromUrl) {
        try { sessionStorage.setItem(STORAGE_KEY, fromUrl); } catch (e) { /* private mode */ }
        // Remove from URL bar so token doesn't get logged by analytics/proxies
        url.searchParams.delete('token');
        try { window.history.replaceState({}, document.title, url.toString()); } catch (e) {}
        return fromUrl;
      }
    } catch (e) { /* SSR / odd URL */ }

    // 2. sessionStorage
    try {
      const stored = sessionStorage.getItem(STORAGE_KEY);
      if (stored) return stored;
    } catch (e) {}

    // 3. Explicit global assigned by host
    if (typeof window.__viewerToken === 'string' && window.__viewerToken) {
      return window.__viewerToken;
    }
    return '';
  }

  const token = discoverToken();
  window.__viewerToken = token;

  // Dev mode short-circuit — no token, leave native APIs alone.
  if (!token) return;

  const ORIGIN = window.location.origin;

  function isSameOrigin(input) {
    try {
      const u = new URL(input, ORIGIN);
      return u.origin === ORIGIN;
    } catch (e) {
      // Relative path (e.g. '/depth') — same-origin by default.
      return typeof input === 'string' && input.startsWith('/');
    }
  }

  function appendTokenQuery(input) {
    try {
      const u = new URL(input, ORIGIN);
      if (!u.searchParams.get('token')) u.searchParams.set('token', token);
      // Return relative path for same-origin to preserve original style.
      return u.origin === ORIGIN ? (u.pathname + u.search + u.hash) : u.toString();
    } catch (e) {
      // Already a query-ridden raw string — append fallback.
      const sep = input.indexOf('?') === -1 ? '?' : '&';
      return input + sep + 'token=' + encodeURIComponent(token);
    }
  }

  // ── fetch wrapper ─────────────────────────────────────────────────
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    try {
      const url = (input && typeof input === 'object' && 'url' in input) ? input.url : input;
      if (typeof url === 'string' && isSameOrigin(url)) {
        const opts = Object.assign({}, init || {});
        const headers = new Headers(opts.headers || (input && input.headers) || {});
        if (!headers.has(HEADER_NAME)) headers.set(HEADER_NAME, token);
        opts.headers = headers;
        return nativeFetch(input, opts);
      }
    } catch (e) {
      // Fall through to native fetch unmodified on any wrapper failure —
      // we'd rather degrade auth than break the request entirely.
    }
    return nativeFetch(input, init);
  };

  // ── EventSource wrapper ───────────────────────────────────────────
  const NativeES = window.EventSource;
  if (NativeES) {
    function PatchedEventSource(url, init) {
      const finalUrl = (typeof url === 'string' && isSameOrigin(url))
        ? appendTokenQuery(url) : url;
      return new NativeES(finalUrl, init);
    }
    PatchedEventSource.prototype = NativeES.prototype;
    PatchedEventSource.CONNECTING = NativeES.CONNECTING;
    PatchedEventSource.OPEN = NativeES.OPEN;
    PatchedEventSource.CLOSED = NativeES.CLOSED;
    window.EventSource = PatchedEventSource;
  }

  // ── WebSocket wrapper ─────────────────────────────────────────────
  const NativeWS = window.WebSocket;
  if (NativeWS) {
    function PatchedWebSocket(url, protocols) {
      let finalUrl = url;
      if (typeof url === 'string') {
        try {
          const u = new URL(url);
          const wsOrigin = (u.protocol === 'wss:' ? 'https:' : 'http:') + '//' + u.host;
          if (wsOrigin === ORIGIN && !u.searchParams.get('token')) {
            u.searchParams.set('token', token);
            finalUrl = u.toString();
          }
        } catch (e) {}
      }
      return protocols ? new NativeWS(finalUrl, protocols) : new NativeWS(finalUrl);
    }
    PatchedWebSocket.prototype = NativeWS.prototype;
    PatchedWebSocket.CONNECTING = NativeWS.CONNECTING;
    PatchedWebSocket.OPEN = NativeWS.OPEN;
    PatchedWebSocket.CLOSING = NativeWS.CLOSING;
    PatchedWebSocket.CLOSED = NativeWS.CLOSED;
    window.WebSocket = PatchedWebSocket;
  }
})();
