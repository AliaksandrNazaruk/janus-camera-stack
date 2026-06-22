#!/usr/bin/env python3
"""camera-node-agent — minimal steady-state control plane on a camera node.

Dependency-free (stdlib only — nodes have python3 but not FastAPI). Lets the
gateway drive FDIR recovery + re-probe WITHOUT SSH, so a remote stream can be
auto-restarted (today RemoteNodeClientStub is inert). Runs as root via systemd
so it can `systemctl restart` the encoders.

Endpoints:
  GET  /healthz                  -> {ok, bundle_version, serials}        (no auth — matches node_client.probe_agent)
  POST /restart_stream?sensor=S  -> systemctl restart rs-stream@S        (X-Node-Token)
  POST /stop_stream?sensor=S     -> systemctl stop rs-stream@S           (X-Node-Token)
  GET  /tuning?sensor=S          -> {width,height,fps,rotation,bitrate_kbps}  (X-Node-Token)
  POST /tuning?sensor=S          -> write rs-S.tuning.env + restart rs-stream@S (X-Node-Token)
  GET  /probe_devices            -> realsense probe JSON                 (X-Node-Token)

Security: bind + token come from /etc/robot/node-agent.env. Reachable-only-from-
gateway (firewall) + a strong token are P3 hardening; an unset token logs a
warning and allows (dev/bench only).
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")

PORT = int(os.getenv("NODE_AGENT_PORT", "8901"))
BIND = os.getenv("NODE_AGENT_BIND", "0.0.0.0")          # P3: bind to the node LAN IP + firewall
TOKEN = os.getenv("NODE_AGENT_TOKEN", "")
PROBE = os.getenv("NODE_PROBE_CLI", "/usr/local/bin/realsense_probe_cli.py")
VERSION_FILE = os.getenv("NODE_BUNDLE_VERSION_FILE", "/etc/robot/node-bundle.version")
SENSORS = ("color", "depth", "ir1", "ir2")


def _bundle_version() -> str:
    try:
        with open(VERSION_FILE) as f:
            for line in f:
                if line.startswith("BUNDLE_VERSION="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _serials() -> list:
    try:
        out = subprocess.run(["python3", PROBE, "--json"], capture_output=True, text=True, timeout=15)
        return [d.get("serial") for d in json.loads(out.stdout or "{}").get("devices", [])]
    except Exception:
        return []


# ── stream tuning (rs-{sensor}.tuning.env, read by rs-stream.sh) ──────────
ENV_DIR = os.getenv("ROBOT_ENV_DIR", "/etc/robot")
_TUNING_RANGE = {"width": (160, 4096), "height": (120, 2160), "fps": (1, 120), "bitrate_kbps": (100, 20000)}
_TUNING_ENVKEY = {"width": "WIDTH", "height": "HEIGHT", "fps": "FPS", "rotation": "ROTATION", "bitrate_kbps": "BITRATE_KBPS"}


def _tuning_path(sensor: str) -> str:
    return os.path.join(ENV_DIR, "rs-%s.tuning.env" % sensor)


def _read_tuning_env(sensor: str) -> dict:
    out = {}
    try:
        with open(_tuning_path(sensor)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _read_tuning(sensor: str) -> dict:
    env = _read_tuning_env(sensor)

    def i(k, d):
        try:
            return int(env.get(k, d))
        except (TypeError, ValueError):
            return d
    return {"width": i("WIDTH", 640), "height": i("HEIGHT", 480), "fps": i("FPS", 15),
            "rotation": i("ROTATION", 0), "bitrate_kbps": i("BITRATE_KBPS", 900)}


def _validate_tuning(body: dict):
    """(env-updates, error). Only provided keys are validated + updated."""
    updates = {}
    for k, ek in _TUNING_ENVKEY.items():
        if body.get(k) is None:
            continue
        try:
            v = int(body[k])
        except (TypeError, ValueError):
            return None, "%s must be an integer" % k
        if k == "rotation":
            if v not in (0, 90, 180, 270):
                return None, "rotation must be 0/90/180/270"
        else:
            lo, hi = _TUNING_RANGE[k]
            if not (lo <= v <= hi):
                return None, "%s out of range [%d,%d]" % (k, lo, hi)
        updates[ek] = str(v)
    if not updates:
        return None, "no tuning fields provided"
    return updates, None


def _write_tuning(sensor: str, updates: dict) -> None:
    """Merge updates into rs-{sensor}.tuning.env (preserving other keys), atomic."""
    env = _read_tuning_env(sensor)
    env.update(updates)
    os.makedirs(ENV_DIR, exist_ok=True)
    path = _tuning_path(sensor)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for k in sorted(env):
            f.write('%s="%s"\n' % (k, env[k]))
    os.replace(tmp, path)


def _list_modes(sensor: str) -> list:
    """Supported [{width,height,fps:[...]}] for a sensor, from the RealSense SDK via the probe CLI
    (`--modes`). Enumeration only (no stream open), so it is safe while the mux holds the camera.
    Returns [] on any error so the gateway/console degrade gracefully."""
    try:
        out = subprocess.run(["python3", PROBE, "--json", "--modes"],
                             capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return []
    for d in data.get("devices", []):
        modes = (d.get("modes") or {}).get(sensor)
        if modes:
            return modes
    return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # quiet
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not TOKEN:
            return True  # dev/bench: unset token allows (set NODE_AGENT_TOKEN in prod)
        return hmac.compare_digest(self.headers.get("X-Node-Token", ""), TOKEN)  # constant-time

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            # Unauthenticated reachability probe (node_client.probe_agent) — keep it
            # MINIMAL. No device serials here (LAN info leak, review H4); serials are
            # on the token-gated /probe_devices.
            self._send(200, {"ok": True, "bundle_version": _bundle_version()})
        elif path == "/tuning":
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            sensor = parse_qs(urlparse(self.path).query).get("sensor", [""])[0]
            if sensor not in SENSORS:
                return self._send(400, {"error": "invalid sensor %r" % sensor})
            self._send(200, _read_tuning(sensor))
        elif path == "/probe_devices":
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            try:
                out = subprocess.run(["python3", PROBE, "--json"], capture_output=True, text=True, timeout=15)
                self._send(200, json.loads(out.stdout or "{}"))
            except Exception as e:  # noqa: BLE001
                self._send(500, {"error": str(e)})
        elif path == "/modes":
            # Supported encoder modes for a sensor (resolution/fps) so the gateway console can offer
            # a real dropdown for a REMOTE node — not just the current value.
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            sensor = parse_qs(urlparse(self.path).query).get("sensor", [""])[0]
            if sensor not in SENSORS:
                return self._send(400, {"error": "invalid sensor %r" % sensor})
            self._send(200, {"sensor": sensor, "modes": _list_modes(sensor)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/tuning":
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            sensor = parse_qs(parsed.query).get("sensor", [""])[0]
            if sensor not in SENSORS:
                return self._send(400, {"error": "invalid sensor %r" % sensor})
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send(400, {"error": "invalid JSON body"})
            updates, err = _validate_tuning(body if isinstance(body, dict) else {})
            if err:
                return self._send(400, {"error": err})
            try:
                _write_tuning(sensor, updates)
                r = subprocess.run(["systemctl", "restart", "rs-stream@%s.service" % sensor],
                                   capture_output=True, text=True, timeout=30)
                ok = r.returncode == 0
                self._send(200 if ok else 500,
                           {"ok": ok, "tuning": _read_tuning(sensor),
                            "detail": (r.stderr.strip() or "applied")[:200]})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "detail": str(e)})
            return
        # restart (recovery) + stop (operator): same shape, different systemctl verb.
        verb = {"/restart_stream": "restart", "/stop_stream": "stop"}.get(parsed.path)
        if verb is not None:
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            sensor = parse_qs(parsed.query).get("sensor", [""])[0]
            if sensor not in SENSORS:
                return self._send(400, {"error": f"invalid sensor {sensor!r}"})
            try:
                r = subprocess.run(["systemctl", verb, f"rs-stream@{sensor}.service"],
                                   capture_output=True, text=True, timeout=30)
                ok = r.returncode == 0
                self._send(200 if ok else 500,
                           {"ok": ok, "detail": (r.stderr.strip() or f"{verb}ed")[:200]})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "detail": str(e)})
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    # Fail-closed (review H4): an unset token must NEVER be exposed on the LAN.
    # Without a token the control endpoints (restart_stream / probe_devices) are
    # unauthenticated; combined with the default 0.0.0.0 bind that's open control
    # of the camera node to the whole LAN. Refuse a non-loopback bind unless a
    # token is set; loopback-only stays allowed for dev.
    if not TOKEN and BIND not in _LOOPBACK_BINDS:
        print(f"[node-agent] FATAL: NODE_AGENT_TOKEN unset and bind {BIND!r} is not "
              f"loopback — refusing to serve UNAUTHENTICATED control on the LAN. "
              f"Set NODE_AGENT_TOKEN (provision does this), or bind 127.0.0.1 for dev.",
              file=sys.stderr, flush=True)
        sys.exit(1)
    if not TOKEN:
        print("[node-agent] WARNING: NODE_AGENT_TOKEN unset — control endpoints "
              "unauthenticated; bound to loopback only (dev/bench).", flush=True)
    print(f"[node-agent] listening on {BIND}:{PORT}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
