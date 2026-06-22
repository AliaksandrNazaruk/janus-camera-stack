# Building the offline aarch64 `pyrealsense2` wheel (bare-node prerequisite)

`build-bundle.sh` ships an offline wheel from `janus_camera_page/installer/wheels/pyrealsense2-*.whl`
if present; otherwise the node's `bootstrap.sh install_pyrealsense` falls back to `pip install`
(needs network, and there is **no usable aarch64 wheel on PyPI** — confirmed
2026-06-19: `pip download pyrealsense2 --platform manylinux2014_aarch64` → *No matching
distribution*). For truly offline / bare nodes the wheel must be **built from source**.

## Why it isn't a quick extract
On a working node (`.55`, 2026-06-19) `pyrealsense2` is **v2.56.5 built from librealsense
source**: a bare extension module `…/dist-packages/pyrealsense2.cpython-313-aarch64-linux-gnu.so`
with **no pip dist-info** (so nothing to `pip wheel`) and a runtime link against
**`librealsense2.so`** (the C++ SDK). A correct wheel must therefore bundle that runtime
(`auditwheel`) and match the **target node's Python** (`.55` runs 3.13; the gateway runs
3.12 — wheels are `cpXY`-specific, build for the node's interpreter).

## Recipe (run on an aarch64 BUILD HOST or the node itself — NOT the prod gateway mid-operation)
Pin a librealsense tag matching the deployed runtime (e.g. `v2.56.5`). `FORCE_RSUSB_BACKEND=ON`
avoids needing kernel patches (RSUSB/libuvc backend), which suits a generic node.

```bash
sudo apt-get install -y git cmake build-essential libssl-dev libusb-1.0-0-dev \
    libudev-dev pkg-config python3-dev patchelf
pip3 install --upgrade build auditwheel
git clone --depth 1 -b v2.56.5 https://github.com/IntelRealSense/librealsense
cmake -S librealsense -B build \
    -DBUILD_PYTHON_BINDINGS=ON -DFORCE_RSUSB_BACKEND=ON \
    -DCMAKE_BUILD_TYPE=Release -DPYTHON_EXECUTABLE="$(command -v python3)"
cmake --build build -j"$(nproc)"           # 30–60 min on a Pi; can OOM — give it swap
# build/Release/ has pyrealsense2*.so + librealsense2.so. Package into a wheel
# (a thin setup.py placing the .so under pyrealsense2/), then bundle the runtime:
auditwheel repair dist/pyrealsense2-2.56.5-*.whl -w janus_camera_page/installer/wheels/
```

## Verify before trusting
- `pip install --no-index --find-links janus_camera_page/installer/wheels pyrealsense2` on a
  CLEAN node of the same arch+Python, then `python3 -c "import pyrealsense2; print(pyrealsense2.__version__)"`.
- Confirm `auditwheel show` lists `librealsense2.so` as bundled (else the import fails on a node
  without librealsense installed — the whole point of "offline").
- Then `bootstrap.sh install_pyrealsense` finds it via `--no-index --find-links` (no network).

## Status
RESEARCHED + recipe documented (2026-06-19). NOT built — needs a build host + iteration on the
packaging/auditwheel step (the load-bearing part). The `.55`/gateway nodes already have a working
source-built `pyrealsense2`, so this only blocks **bare** nodes.
