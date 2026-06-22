#!/usr/bin/env bash
# build-pyrealsense.sh — build pyrealsense2 wheel from librealsense source.
#
# Use this on the target hardware (Pi 4/5 / SBC) to produce a wheel that
# can then be vendored in installer/wheels/.
#
# Usage:
#   ./build-pyrealsense.sh                 # latest released version
#   ./build-pyrealsense.sh 2.55.1          # specific version tag
#   ./build-pyrealsense.sh --no-install    # build wheel only, don't pip-install
#
# Output: ./pyrealsense2-VERSION-cpXY-cpXY-linux_ARCH.whl

set -euo pipefail

VERSION="${1:-2.55.1}"
[ "${VERSION}" = "--help" ] && { sed -n '2,15p' "$0"; exit 0; }
[ "${VERSION}" = "--no-install" ] && { NO_INSTALL=1; VERSION="${2:-2.55.1}"; } || NO_INSTALL=0

WORK_DIR="${WORK_DIR:-/tmp/build-pyrealsense}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3)}"

echo "▸ Building pyrealsense2 ${VERSION}"
echo "  Python:    ${PYTHON_BIN} ($(${PYTHON_BIN} --version))"
echo "  Work dir:  ${WORK_DIR}"
echo ""

# ── apt deps ──
echo "▸ Installing build dependencies..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  cmake build-essential python3-dev \
  libssl-dev libusb-1.0-0-dev libudev-dev pkg-config \
  libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev \
  git

# ── librealsense source ──
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

if [ ! -d librealsense ]; then
  echo "▸ Cloning librealsense..."
  git clone --depth 1 --branch "v${VERSION}" \
    https://github.com/IntelRealSense/librealsense.git
fi

cd librealsense
git checkout "v${VERSION}" 2>/dev/null || { echo "Tag v${VERSION} not found"; exit 1; }

# ── CMake build ──
echo "▸ Building librealsense + Python bindings..."
mkdir -p build && cd build
cmake .. \
  -DBUILD_EXAMPLES=OFF \
  -DBUILD_GRAPHICAL_EXAMPLES=OFF \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DPYTHON_EXECUTABLE="${PYTHON_BIN}" \
  -DBUILD_WITH_OPENMP=ON \
  -DCMAKE_BUILD_TYPE=Release

make -j"$(nproc)" pyrealsense2

# ── Install udev rules (camera permissions) ──
echo "▸ Installing udev rules..."
cd ..
sudo cp config/99-realsense-libusb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules || true
sudo udevadm trigger || true

# ── Build wheel ──
echo "▸ Building wheel..."
cd wrappers/python
"${PYTHON_BIN}" -m pip install --upgrade build wheel
"${PYTHON_BIN}" -m build --wheel

WHEEL="$(ls dist/pyrealsense2-*.whl | head -1)"
[ -z "${WHEEL}" ] && { echo "ERROR: wheel build failed"; exit 1; }

OUT_DIR="$(dirname "$(readlink -f "$0")")/wheels"
mkdir -p "${OUT_DIR}"
cp "${WHEEL}" "${OUT_DIR}/"

echo ""
echo "▸ Wheel built: ${OUT_DIR}/$(basename "${WHEEL}")"
ls -lh "${OUT_DIR}/$(basename "${WHEEL}")"

# ── Smoke test ──
if [ "${NO_INSTALL}" = "0" ]; then
  echo ""
  echo "▸ Smoke test..."
  "${PYTHON_BIN}" -m venv /tmp/pyrs-test-venv
  /tmp/pyrs-test-venv/bin/pip install -q "${OUT_DIR}/$(basename "${WHEEL}")"
  /tmp/pyrs-test-venv/bin/python -c "
import pyrealsense2 as rs
print(f'pyrealsense2 version: {rs.__version__}')
ctx = rs.context()
devices = list(ctx.devices)
print(f'connected RealSense devices: {len(devices)}')
for d in devices:
    print(f'  {d.get_info(rs.camera_info.name)} (serial: {d.get_info(rs.camera_info.serial_number)})')
"
  rm -rf /tmp/pyrs-test-venv
fi

echo ""
echo "Next: copy to installer/wheels/ and commit"
echo "  cp ${OUT_DIR}/$(basename "${WHEEL}") <repo>/janus_camera_page/installer/wheels/"
