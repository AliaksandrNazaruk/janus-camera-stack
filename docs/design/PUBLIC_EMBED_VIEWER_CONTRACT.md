# Public Embed Viewer Contract — Cycle 16C recon + design note

**Status:** RECON COMPLETE — awaiting GO. No production code yet. This cycle changes **who can see
the camera**, so the security-policy gate (§7 D1) must be answered before any code.

**Problem (correct framing):** the host-side media path is proven live (Cycles 16A/16B), but there is
no clean *viewer-delivery contract*. The only way to view a stream externally today is
`/preview/{mp_id}?token=<static VIEWER_TOKEN>` — a debugging workaround: it puts a long-lived secret in
the URL/history, forces the external frontend to understand Janus/token internals, and breaks because
WS auth and HTTP auth diverge. A product needs: **external frontend → `streamId` → video**, with no
manual Janus/token handling.

## 1. Current state (from code)

- Routes: `/color_view.html`, `/preview/{mp_id}` (viewer-gated), `/multiview.html`. **No `/embed`, no
  `/color`.** `/preview/{mp_id}` already renders the player for an arbitrary stream via
  `_render_color_view_variant(stream_id, stream_name)`; the player reads `data-prefer-stream-id`.
- Auth matrix (the core gap):

  | credential | HTTP `require_viewer` | WS `require_viewer_ws` |
  |---|---|---|
  | `X-Admin-Token` header | ✅ | ❌ |
  | `cam_admin` **session cookie** (`session_store.is_valid`) | ✅ | ❌ ignored |
  | `X-Viewer-Token` header | ✅ | ✅ (browsers can't set on WS) |
  | `?token=` (static `VIEWER_TOKENS`) | ✅ | ✅ |

  So the WS only accepts the **static viewer token**; the session cookie an authenticated browser
  already has is ignored. That is why "the page loads but the WS 403s."
- `session_store` already provides exactly the needed primitive: opaque, short-lived, server-side
  session ids (`create_session(ttl)` / `is_valid` / `revoke`, 12 h default). Used today only for the
  `cam_admin` cookie, issued in `ui_viewmodel.py` as `set_cookie(ADMIN_COOKIE, sid, httponly, secure,
  samesite="lax", path="/")`.
- `viewer_auth_bootstrap.js` already injects a token from `window.__viewerToken` into the WS (`?token=`)
  and fetch (`X-Viewer-Token`). **Reusable** — if the server injects an ephemeral token into the page.
- CSP `frame-ancestors` (app/core/app.py) = `'self' https://*.example.com <LAN>` (env
  `CSP_FRAME_ANCESTORS_LAN`). Arbitrary external embedding origins are NOT allowed yet.

## 2. The target contract

```
GET /embed/streams/{stream_id}      →  renders the player for that stream, self-contained
```
External frontend:
```html
<iframe src="https://console.example.com/embed/streams/1305"
        allow="autoplay; fullscreen"></iframe>
```
The **server** handles the viewer session/auth internally; the frontend never touches Janus or tokens.

## 3. Two mechanisms to make the browser stop handling tokens

**Mechanism A — viewer session cookie (`cam_viewer`).** `/embed/...` mints `session_store.create_session`,
sets `cam_viewer`; extend `require_viewer` + `require_viewer_ws` to accept it (WS handshakes send
cookies). Clean for **same-origin / top-level** embeds.
**Constraint:** a cross-origin `<iframe>` makes the cookie third-party → must be `SameSite=None; Secure`,
and modern browsers (Chrome third-party-cookie phase-out, Safari ITP) often **block** it → the WS
handshake wouldn't carry it. Fragile for true cross-site embedding.

**Mechanism B — ephemeral viewer token injected into the page (recommended).** `/embed/...` mints a
short-lived token and renders `<script nonce>window.__viewerToken="<ephemeral>"</script>`. The existing
`viewer_auth_bootstrap.js` appends it as `?token=` to the WS and `X-Viewer-Token` to fetch.
`require_viewer`/`require_viewer_ws` validate the ephemeral token (short-TTL store, or HMAC-signed).
**Advantages:** no cookie → no third-party-cookie breakage → works inside cross-origin iframes; reuses
the existing bootstrap + WS `?token=` path wholesale. The "token in URL" objection is mitigated: it is
**ephemeral, per-load, server-injected** — not a static secret the frontend manages.

Recommended: **B** (robust in iframes, max reuse), optionally + the **WS session-cookie parity** from A
(so logged-in operators on same-origin also "just work"). Both can validate via `session_store`.

## 4. Minimal design (Mechanism B + WS cookie parity)

1. **`GET /embed/streams/{stream_id}`** — validates the embed policy (§7 D1), mints an ephemeral viewer
   credential `t = session_store.create_session(EMBED_TTL)` (short, e.g. 2 h), renders the existing
   player variant for `stream_id` with `window.__viewerToken = t` injected (CSP-nonce'd inline script).
2. **`require_viewer` + `require_viewer_ws`**: accept a valid `session_store` id supplied as the viewer
   credential (via `?token=`/`X-Viewer-Token` for WS+fetch, AND via the `cam_viewer`/`cam_admin` cookie
   for same-origin). One rule, applied to both HTTP and WS — closing the matrix gap in §1.
3. **CSP `frame-ancestors`**: add the allowed embedding origin(s) (§7 D3), env-driven.
4. Player config already produces same-origin `janusWs = wss://<host>/janus-ws`,
   `janusRest = https://<host>/janus` — no change needed.

This is additive: `/preview/{mp_id}?token=` and the operator console keep working; static
`VIEWER_TOKENS` stays for back-compat.

## 5. De-risk FIRST (one external validation, then build)

The embed layer rides the **same** Janus-WS-through-Cloudflare + TURN-relay path we have **not yet
proven delivers frames to an off-LAN browser** (`frames_decoded` has only ever been 0). If that path
is broken (e.g. Cloudflare WS forwarding — the tunnel is thin), `/embed` will render and still show no
video. **Recommended Step 0:** open `…/color_view.html?token=<VALID viewer token>` from LTE/5G once and
confirm `frames_decoded > 0`. ~5 min; de-risks the whole cycle. This is *not* "manual token as the
product" — it is proving the media path before building the contract on top of it.

### 5.1 De-risk done from the host (2026-06-22) — Cloudflare path is NOT the blocker

Probed the public path from the host (no browser needed):
- **WS upgrade through Cloudflare** `wss://console.example.com/janus-ws` (no token) →
  `HTTP/1.1 403 Forbidden, cf-cache-status: DYNAMIC` = L4's own `require_viewer_ws` 403 returned
  **through the tunnel**. So **Cloudflare forwards the WebSocket upgrade to the origin** — the tunnel
  is not the blocker (a tunnel failure would be 502/520/timeout, not an origin 403).
- **HTTPS through Cloudflare** `/healthz` → 403 for a header-less request but **HTTP/2 200 with a
  browser User-Agent** (both `console.` and `cameras.example.com`). The 403 was Cloudflare
  **bot-filtering**, not an Access gate. **No Cloudflare Access/Zero-Trust gate** sits in front — a real
  browser (and an iframe) reaches the origin.

**Residual unknown narrowed to one hop:** does the **TURN relay actually deliver decoded frames to an
off-LAN browser** (`frames_decoded > 0`)? That is the only thing requiring a real external browser —
Step 0 below. Everything else on the path is proven.

## 6. Red lines

- No camera / encoder / RTP / TURN / Janus / Cloudflare changes (this is signaling-delivery only).
- Reuse `session_store`; **no new generic session/token framework.**
- Don't weaken existing auth: `/preview`, admin routes, static `VIEWER_TOKENS`, and the WS gate stay;
  embed is additive. The ephemeral credential must be short-TTL and revocable.
- Ephemeral token only ever **server-injected** into the embed page — never required from the external
  frontend, never a static secret in a URL.

## 7. Gate decisions (need GO before code)

- **D1 — embed authorization policy (THE security decision).**
  (a) **Public** — anyone with `/embed/streams/{id}` gets an ephemeral session → camera publicly
  viewable by URL. Simplest; *reverses* the campaign's "no unauthenticated access" posture.
  (b) **Signed link** — `/embed/streams/{id}?sig=<HMAC>` minted by the external frontend's *backend*
  (holds a shared secret); the browser/JS still handles nothing. Non-public, capability-based.
  *[recommended — matches "frontend embeds, server controls access"]*
  (c) **Origin-gated** — issue a session only if the embedding origin ∈ allowlist (Referer/`Sec-Fetch`
  + frame-ancestors). Soft (Referer spoofable).
  (d) **Operator-only** — embed requires a `cam_admin`/viewer session (not really "public embed").
- **D2 — credential mechanism.** (a) **Ephemeral token + window.__viewerToken (Mechanism B)**
  [recommended]; (b) viewer session cookie (Mechanism A); (c) both (B for cross-origin, A for
  same-origin parity).
- **D3 — which embedding origins** get added to CSP `frame-ancestors` (exact list, env-driven). Public
  embed implies `*` or a broad set — couples to D1.
- **D4 — sequencing.** (a) **De-risk Step 0 first** (prove external `frames_decoded>0` with a valid
  token), then build embed [recommended]; (b) build embed immediately and validate at the end.
- **D5 — WS cookie parity** (`require_viewer_ws` accept `cam_admin`/`cam_viewer` session cookie): fold
  into this cycle, or keep as the separate small Cycle-16B fix?
