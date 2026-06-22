# Security policy

## Supported versions

Security fixes are released on:
- The latest minor version (e.g., if 1.3.x is current, 1.3.x receives fixes)
- The most recent prior minor version for 6 months after the new minor release

Older versions: please upgrade.

## Reporting a vulnerability

**DO NOT open a public issue.**

Email: security@example.com

Encrypted reporting (optional): PGP key fingerprint published at
https://example.com/.well-known/pgp-key.

What to include:
- Affected component (L0/L1/L2/L3/L4, specific file, version)
- Reproducer (steps + expected vs actual)
- Impact assessment (auth bypass, RCE, data leak, DoS, etc.)
- Suggested mitigation if you have one

## Response timeline

- Acknowledgement: within 48 hours
- Initial assessment: within 5 business days
- Fix + advisory published: within 90 days for critical, 180 days for high

If you don't hear back within 48 hours, please follow up — email can fail.

## Disclosure

We follow coordinated disclosure:
1. Reporter and maintainers agree on a fix timeline
2. Patch developed privately
3. Security advisory drafted (CVE requested if applicable)
4. Patch released in a versioned tag
5. Advisory + reporter credit published 7 days after the patched release
6. Public disclosure of details

We credit reporters in release notes unless they prefer anonymity.

## Known attack surface

This stack handles:
- Untrusted WebRTC traffic (browser clients)
- Untrusted RTP from edge encoder nodes (if improperly firewalled)
- Operator commands from the dashboard (admin auth required)
- Plugin code from `/etc/robot/plugins.d/` (treated as trusted — operator-controlled)

Out-of-scope:
- Compromise of the host OS (kernel, systemd) — out of scope
- DoS via plugin code — operator's responsibility
- Janus Gateway or coturn vulnerabilities — report upstream

## Hardening recommendations (operators)

Before shipping to production, see
[docs/DEPLOYMENT_CLOUD.md](docs/DEPLOYMENT_CLOUD.md) "Production hardening
checklist".

Notable items:
- Rotate `INTERNAL_API_SECRET`, `TURN_SECRET`, `JANUS_ADMIN_SECRET` per-deployment
- Run camera-page as non-root (Docker image does this by default)
- Restrict network policies — L4 should not be reachable from untrusted networks
  without a reverse proxy enforcing auth
- Keep dependencies updated (`pip-audit` periodically)
- Enable Janus admin secret in jcfg (not commented out)

### Network exposure contract

Only the L4 API (port 8900) should ever be reachable from the public internet,
and only through a reverse proxy / tunnel that terminates TLS and enforces auth
(cloudflared, nginx, Caddy). Everything else is a backend the browser reaches
**only** through L4's `/janus`, `/janus-ws` and `/depth` proxies.

| Port | Service | Public internet | LAN | Required posture |
|---|---|---|---|---|
| 8900 | L4 FastAPI (camera-page) | via reverse proxy / tunnel only | ok behind auth | TLS + auth at the proxy |
| 8088 | Janus HTTP | **never** | **no** | localhost (single-host) or private container network |
| 8188 | Janus WebSocket | **never** | **no** | localhost (single-host) or private container network |
| 7088 | Janus admin API | **never** | **no** | localhost only (`admin_ip = "127.0.0.1"`) |
| 8910 | textroom_relay | **never** | **no** | localhost / private network |
| 3478 / 5349 | coturn STUN/TURN | yes (UDP/TCP relay) | yes | needed for NAT traversal; auth via ephemeral HMAC creds |

Deployment notes:
- **Single-host (Pi / bare metal):** bind Janus HTTP/WS to `127.0.0.1` in
  `janus.transport.http.jcfg` / `janus.transport.websockets.jcfg` (`ip` /
  `ws_interface`). L4 and the encoders reach Janus over loopback. A host
  firewall must DROP 8088/8188/8910 from non-loopback as defense in depth.
- **Container (compose / k8s):** Janus must bind `0.0.0.0` so the L4 container
  can reach it across the bridge/pod network — isolation comes from the Docker
  network or a k8s `NetworkPolicy`, **not** from the bind address. Do not publish
  (`ports:`) 8088/8188/7088/8910 to the host.
- The browser never holds Janus-HTTP, Janus-admin, or relay semantics; it only
  speaks WebRTC + the L4 proxy endpoints. If a deployment exposes 8088/8188 to
  clients, that is a misconfiguration — fix the bind/firewall, do not add auth there.
