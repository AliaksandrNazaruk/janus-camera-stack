# Vendored Python wheels

This directory ships pre-built Python wheels for packages that are hard
to install via pip on certain platforms — primarily **pyrealsense2** on
ARM (Raspberry Pi / SBC) Linux.

`install.sh` auto-detects the host arch + distro and picks the matching
wheel here. If no matching wheel is found, installer falls back to PyPI
(works on amd64), then to printing manual build instructions.

## Naming convention

Standard pip wheel naming:
```
{package}-{version}-{python}-{abi}-{platform}.whl
```

Examples:
- `pyrealsense2-2.55.1-cp312-cp312-linux_aarch64.whl` — Pi 5/4, Ubuntu 24.04, Python 3.12
- `pyrealsense2-2.54.0-cp310-cp310-linux_aarch64.whl` — Pi 4, Ubuntu 22.04, Python 3.10
- `pyrealsense2-2.55.1-cp312-cp312-linux_x86_64.whl` — generic amd64, Python 3.12

## Matching matrix

| Host | Python | Wheel name pattern | Source |
|---|---|---|---|
| Pi 4/5 Ubuntu 24.04 arm64 | 3.12 | `*cp312-cp312-linux_aarch64.whl` | Build from source ([see below](#building-pyrealsense2-from-source)) |
| Pi 4 Ubuntu 22.04 arm64 | 3.10 | `*cp310-cp310-linux_aarch64.whl` | Build from source |
| Generic amd64 | 3.10/3.12 | (not vendored) | PyPI `pip install pyrealsense2` |
| Pi 3 (armv7l) | any | `*cp3xx-cp3xx-linux_armv7l.whl` | Build from source (slow!) |
| Apple Silicon | any | (not supported) | Use Linux VM / Docker |

## Why vendor wheels

Intel ships pyrealsense2 wheels to PyPI **for amd64 Linux only**. ARM users
must build from source — which requires:
- ~300MB build deps (cmake, libusb, libssl, libudev, build-essential)
- 15-45 minutes on Pi (CPU-bound C++ compile)
- Correct CMake flags
- librealsense version matching the Intel SDK level

Vendoring a pre-built wheel reduces fresh install from ~30min to ~10sec.

## Building pyrealsense2 from source

The exact steps that produce the wheel are codified in `build-pyrealsense.sh`
(see below). Re-run when:
- New Pi OS major version released
- librealsense major version released (e.g., 2.56.x)
- Bumping minimum supported Python

```bash
# Run on the TARGET hardware (cross-compile is fragile for librealsense)
./build-pyrealsense.sh 2.55.1   # arg = librealsense tag

# Output: ./pyrealsense2-2.55.1-cpXY-cpXY-linux_aarch64.whl
# Move to this dir and commit.
```

## Wheel verification

After building, verify before committing:
```bash
# Install in a throwaway venv
python3 -m venv /tmp/test-venv
/tmp/test-venv/bin/pip install ./pyrealsense2-*.whl

# Smoke test
/tmp/test-venv/bin/python -c "
import pyrealsense2 as rs
print('version:', rs.__version__)
ctx = rs.context()
print('connected devices:', [d.get_info(rs.camera_info.name) for d in ctx.devices])
"
# Should print 'version: 2.55.1' and list connected D435/D455/etc.

rm -rf /tmp/test-venv
```

## Adding a wheel to the repo

```bash
# 1. Build on target hardware (see above)
# 2. Verify (see above)
# 3. Copy + commit
cp pyrealsense2-2.55.1-cp312-cp312-linux_aarch64.whl installer/wheels/
git add installer/wheels/pyrealsense2-*.whl
git commit -m "feat: vendor pyrealsense2 2.55.1 wheel for Pi5/Ubuntu24 arm64"
```

## Wheel licenses

pyrealsense2 is Apache-2.0 licensed (Intel RealSense SDK). librealsense
source is here: https://github.com/IntelRealSense/librealsense.

When redistributing vendored wheels, preserve LICENSE.txt from the source
tarball (auto-included by `pip wheel`).

## Currently vendored

(empty — operators build per target. See [INSTALL.md](../../INSTALL.md)
for manual build steps.)

To contribute a wheel that worked for you: open a PR and we'll add it.
Include:
- Hardware (e.g., "Pi 5 8GB")
- OS (e.g., "Ubuntu 24.04.3 LTS")
- Python version
- librealsense version
- SHA256 + smoke test output
