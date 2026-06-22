# camera-node bootstrap bundle

The deployable artifact the gateway SSH-pushes to a camera host to bring it up as
a **remote RTP producer**. See [DYNAMIC_CAMERA_ONBOARDING.md](../../janus_camera_page/docs/design/DYNAMIC_CAMERA_ONBOARDING.md).

**Default-deny by construction:** this bundle is node-only. It contains NO Janus,
NO coturn/TURN, NO Cloudflare, and NO secret generation — a node never holds the
gateway's Janus admin secret (review finding S7). Enforced by
`janus_camera_page/tests/test_node_bootstrap.py`.

## Contents (after `build-bundle.sh`)
```
bootstrap.sh                  # node-only installer (default-deny, idempotent, probe-first)
probe/realsense_probe_cli.py  # standalone RealSense probe (no app deps)
files/                        # rs-stream.sh, realsense-mux.py, *.service (from the tested encoder role)
wheels/                       # offline pyrealsense2 wheel (arch-matched) — see prerequisite below
VERSION / SHA256SUMS[.asc]    # version + integrity (+ signature if GPG_KEY set)
```

## Build
```
./build-bundle.sh [OUT_DIR]          # default /tmp/camera-node-bundle (+ .tar.gz)
GPG_KEY=<id> ./build-bundle.sh       # sign SHA256SUMS (review S8)
```

## Use (on the node, P1 manual flow)
```
sudo ./bootstrap.sh --probe-only                          # enumerate cameras, deploy nothing
sudo ./bootstrap.sh --rtp-target-host 192.168.1.10        # deploy stack, RTP -> gateway
```

## P1 prerequisites still open
- **Offline pyrealsense wheel** — `installer/wheels/` is README-only today, so the bundle is not yet
  fully offline for a *bare* host (bootstrap falls back to `pip`). Build the aarch64 wheel for true
  no-internet install. NB: `bootstrap.sh` skips the install entirely when `pyrealsense2` already
  imports (e.g. built on the node — as on `.55`), so an already-prepared node needs no wheel/network.
- **Signing key** — bundle is unsigned unless `GPG_KEY` is set; sign before any non-bench use (S8).
- **Node-agent** (steady-state control / FDIR restart) is **P2**, intentionally absent here.
