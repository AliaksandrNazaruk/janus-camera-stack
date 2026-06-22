#!/usr/bin/env python3
"""RealSense multi-sensor mux (depth + IR1 + IR2 + optional color → FIFOs).

One rs.pipeline owns the D435i и pumps frames из depth, ir1, ir2 (и color,
когда RS_ENABLE_COLOR=1) streams в named FIFOs. Phase 2: color too streams
через mux (rs-stream@color consumer) — the legacy V4L2 path (rtp-rgb@cam-rgb)
was retired.

Key design points:
- **All streams enabled at pipeline.start()** — librealsense constraint:
  add stream к running pipeline невозможен.
- **FIFO writes non-blocking** — каждый sensor consumer (ffmpeg) живёт
  независимо. Если consumer'а нет (rs-depth@.service not running),
  mux молча дропает frame для того FIFO. Other streams не задеты.
- **Colorizer для depth** — z16 → RGB8 (encodable ffmpeg как rgb24).
- **Graceful shutdown по SIGTERM** — pipeline.stop(), close FIFOs.

Output FIFOs (создаются если нет):
  /run/realsense/depth.fifo  ← rgb24 H×W×3
  /run/realsense/ir1.fifo    ← y8   H×W
  /run/realsense/ir2.fifo    ← y8   H×W

Env vars (override defaults):
  RS_DEPTH_WIDTH/HEIGHT/FPS    (default 640×480@15)
  RS_IR_WIDTH/HEIGHT/FPS       (default 640×480@15)
  RS_FIFO_DIR                  (default /run/realsense)
  RS_LOG_STATS_INTERVAL        (default 5.0 sec)
"""
from __future__ import annotations

import errno
import fcntl
import logging
import os
import signal
import struct
import sys
import termios
import threading
import time
from typing import Optional

import numpy as np
import pyrealsense2 as rs


# Linux F_SETPIPE_SZ / F_GETPIPE_SZ fcntl numbers (fcntl module не exposes them).
F_SETPIPE_SZ = 1031
F_GETPIPE_SZ = 1032
# Target pipe buffer: 4MB. Each frame up к ~1MB (depth RGB 640×480×3 = 921KB).
# 4MB → fits 4+ depth frames or 12+ IR frames → atomic writes guaranteed
# unless consumer is severely lagged.
TARGET_PIPE_SIZE = 4 * 1024 * 1024


# ── Config from env ────────────────────────────────────────────────
FIFO_DIR        = os.getenv("RS_FIFO_DIR", "/run/realsense")
DEPTH_WIDTH     = int(os.getenv("RS_DEPTH_WIDTH",  "640"))
DEPTH_HEIGHT    = int(os.getenv("RS_DEPTH_HEIGHT", "480"))
DEPTH_FPS       = int(os.getenv("RS_DEPTH_FPS",    "15"))
IR_WIDTH        = int(os.getenv("RS_IR_WIDTH",     "640"))
IR_HEIGHT       = int(os.getenv("RS_IR_HEIGHT",    "480"))
IR_FPS          = int(os.getenv("RS_IR_FPS",       "15"))
STATS_INTERVAL  = float(os.getenv("RS_LOG_STATS_INTERVAL", "5.0"))

# Color stream через pyrealsense2 (Phase 2 — replaced V4L2 rtp-rgb). Gated by
# RS_ENABLE_COLOR так что depth-only deployments не pay color USB bandwidth.
# On this node it's ON (/etc/robot/rs-mux.env). Historical note: a V4L2 consumer
# of /dev/cam-rgb would USB-conflict с pipeline.start — нет такого больше.
# Set RS_ENABLE_COLOR=1 в /etc/robot/rs-mux.env ONLY after stopping any V4L2
# consumer. См. Phase 2.2 migration runbook.
#
# When enabled:
#   • mux opens color stream в pipeline (RS_COLOR_W/H/FPS)
#   • writes RGB8 frames к /run/realsense/color.fifo (consumed by rs-stream@color)
#   • rs.align computes aligned-depth array per pipeline tick → sample_aligned()
#     can lookup pre-computed value instead of CPU reprojection per query
ENABLE_COLOR    = os.getenv("RS_ENABLE_COLOR", "0").strip() == "1"
# Match DEPTH_FPS: pipeline.wait_for_frames() returns a synchronized frameset
# gated by ALL enabled streams, so color@30 + depth@15 still delivers framesets
# at 15fps — the extra 15 color fps captured but discarded = wasted USB bandwidth.
COLOR_FPS       = int(os.getenv("RS_COLOR_FPS", str(DEPTH_FPS)))
# rs.align idle timeout: align processing (CPU-costly full-frame reproject) runs
# ТОЛЬКО когда aligned-depth queried в последние N seconds. Idle → skip align,
# saves ~half mux CPU когда nobody uses aligned probe. First query после idle
# falls back к CPU reprojection (one-shot), then fast path kicks in для the burst.
ALIGN_IDLE_SEC  = float(os.getenv("RS_ALIGN_IDLE_SEC", "10.0"))

# P1-CV-001: RGB↔depth spatial alignment for click-to-depth API.
# COLOR_WIDTH/HEIGHT — resolution at which V4L2 captures /dev/cam-rgb;
# intrinsics depend on resolution, so calibration must be queried for the
# SAME size that the browser actually receives. Browser passes click as
# percentage (resolution-independent), here we map к color pixel coords.
COLOR_WIDTH     = int(os.getenv("RS_COLOR_WIDTH",  "640"))
COLOR_HEIGHT    = int(os.getenv("RS_COLOR_HEIGHT", "480"))

# Depth-sampling HTTP API (compat с .55 realsense_mux):
# routes/depth.py proxies /api/v1/{cam_type}/depth → http://<this>:RS_HTTP_PORT/depth
HTTP_BIND_HOST = os.getenv("RS_HTTP_BIND_HOST", "127.0.0.1")
HTTP_PORT      = int(os.getenv("RS_HTTP_PORT", "8000"))

# Hardware reset retry budget когда pipeline.start() throws VIDIOC_S_FMT
HW_RESET_RETRIES   = 2
# Phase 1 fix (P1-REL-001): reduced from 10000ms к 2000ms so USB stalls
# surface fast instead of freezing entire pipeline 10sec at a time. Coupled
# with depth sample staleness flag (sampled.ts_age_ms exposed in /depth
# response) so client can detect "no fresh data" rather than silently
# returning ancient depth values.
FRAME_TIMEOUT_MS   = 2000
MAX_CONSEC_TIMEOUTS = 5   # 5×2sec = 10sec total budget — same as before
# Stale-data threshold: if latest depth frame older than this, sampler
# returns с stale=true flag instead of silently passing old values.
DEPTH_STALE_MS = 500

DEPTH_FIFO = os.path.join(FIFO_DIR, "depth.fifo")
IR1_FIFO   = os.path.join(FIFO_DIR, "ir1.fifo")
IR2_FIFO   = os.path.join(FIFO_DIR, "ir2.fifo")
COLOR_FIFO = os.path.join(FIFO_DIR, "color.fifo")  # Phase 2.1, only used когда ENABLE_COLOR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("rs-mux")


# ── FIFO management (non-blocking writes) ──────────────────────────

class FifoWriter:
    """Wraps a FIFO opened O_NONBLOCK | O_RDWR.

    O_RDWR vs O_WRONLY: opening a FIFO O_WRONLY with O_NONBLOCK throws
    ENXIO if no reader present. O_RDWR avoids that — open succeeds
    immediately. Writes return EAGAIN if pipe буффер full (slow consumer),
    мы дропаем frame silently.
    """
    def __init__(self, path: str, label: str):
        self.path = path
        self.label = label
        self.fd: Optional[int] = None
        self.drops = 0
        self.wrote = 0
        self._ensure_fifo()
        self._open()

    def _ensure_fifo(self):
        os.makedirs(os.path.dirname(self.path), mode=0o775, exist_ok=True)
        if os.path.exists(self.path):
            if not os.path.exists(self.path) or os.stat(self.path).st_mode & 0o170000 != 0o010000:
                # Not a FIFO — wipe and recreate
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
        if not os.path.exists(self.path):
            os.mkfifo(self.path, 0o660)

    def _open(self):
        flags = os.O_RDWR | os.O_NONBLOCK
        try:
            self.fd = os.open(self.path, flags)
            # Increase pipe buffer to fit several full frames — avoids partial
            # writes when consumer briefly stalls. Without large buffer, partial
            # write would leave half-frame bytes in pipe, permanently corrupting
            # frame boundary alignment for ffmpeg rawvideo reader.
            try:
                actual = fcntl.fcntl(self.fd, F_SETPIPE_SZ, TARGET_PIPE_SIZE)
                log.info("[%s] FIFO open: %s (pipe buffer %d bytes)",
                         self.label, self.path, actual)
            except OSError as e:
                # /proc/sys/fs/pipe-max-size may cap us; log but proceed
                log.warning("[%s] could not enlarge pipe buffer (%s) — "
                            "atomic-write guard alone will keep alignment",
                            self.label, e)
                log.info("[%s] FIFO open: %s", self.label, self.path)
        except OSError as e:
            log.error("[%s] FIFO open failed (%s): %s", self.label, self.path, e)
            self.fd = None

    def _bytes_in_pipe(self) -> int:
        """How many bytes currently sit в the pipe (not yet read by consumer)."""
        buf = bytearray(4)
        fcntl.ioctl(self.fd, termios.FIONREAD, buf)
        return struct.unpack('I', bytes(buf))[0]

    def _pipe_capacity(self) -> int:
        return fcntl.fcntl(self.fd, F_GETPIPE_SZ)

    def write(self, data: bytes) -> bool:
        """Atomic-or-drop write. Either writes the FULL frame or nothing —
        never partial. Prevents byte-stream misalignment in the reader
        (which causes persistent tiled-output corruption).
        """
        if self.fd is None:
            self.drops += 1
            return False
        try:
            # Atomic-write guard: only attempt if pipe has full frame's
            # worth of free space. If not, drop the entire frame.
            free = self._pipe_capacity() - self._bytes_in_pipe()
            if free < len(data):
                self.drops += 1
                return False
            n = os.write(self.fd, data)
            if n != len(data):
                # Should not happen с capacity check, но if it does, we've
                # already corrupted alignment. Pad with zeros к frame
                # boundary so consumer's NEXT frame still aligns.
                missing = len(data) - n
                try:
                    os.write(self.fd, b"\x00" * missing)
                    log.warning("[%s] partial write (%d/%d) — padded к frame "
                                "boundary; expect 1 corrupt frame, then resync",
                                self.label, n, len(data))
                except OSError:
                    pass
                self.drops += 1
                return False
            self.wrote += 1
            return True
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                self.drops += 1
                return False
            log.warning("[%s] write error (%s) — reopening FIFO", self.label, e)
            self._close_quiet()
            self._open()
            self.drops += 1
            return False

    def _close_quiet(self):
        if self.fd is not None:
            try: os.close(self.fd)
            except OSError: pass
            self.fd = None

    def close(self):
        self._close_quiet()


# ── Pipeline lifecycle с hw-reset retry ────────────────────────────

def _build_config() -> rs.config:
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth,    0, DEPTH_WIDTH, DEPTH_HEIGHT, rs.format.z16, DEPTH_FPS)
    cfg.enable_stream(rs.stream.infrared, 1, IR_WIDTH,    IR_HEIGHT,    rs.format.y8,  IR_FPS)
    cfg.enable_stream(rs.stream.infrared, 2, IR_WIDTH,    IR_HEIGHT,    rs.format.y8,  IR_FPS)
    if ENABLE_COLOR:
        # rgb8 — same byte layout as colorizer output → rs-stream.sh reuses
        # "rgb24" pix_fmt branch. Bandwidth: 640×480×3×30fps = 27.6 MB/s raw
        # (D435i USB protocol may compress internally; observe in practice).
        cfg.enable_stream(rs.stream.color, 0, COLOR_WIDTH, COLOR_HEIGHT,
                          rs.format.rgb8, COLOR_FPS)
    return cfg


def _wait_for_device(timeout_sec: float = 30.0) -> bool:
    """Poll до появления RealSense device (boot/USB-enumeration race).

    После reboot или USB replug mux может стартовать раньше чем udev enumerate'ит
    D435i. Без этого pipeline.start() сразу raise'ит "no device" → mux exits →
    systemd restart churn (могло залочить burst). Ждём device в самом процессе —
    self-heals без restart'ов. Falls through по timeout — pipeline.start() тогда
    raise'ит как раньше (systemd restart как fallback).
    """
    deadline = time.monotonic() + timeout_sec
    waited = False
    while time.monotonic() < deadline:
        try:
            if len(rs.context().query_devices()) > 0:
                if waited:
                    log.info("RealSense device appeared — proceeding")
                return True
        except Exception as e:
            log.debug("query_devices during wait: %s", e)
        waited = True
        log.info("Waiting for RealSense device (USB enumeration)…")
        time.sleep(2)
    log.warning("No RealSense device after %.0fs wait — attempting start anyway", timeout_sec)
    return False


def _start_pipeline_with_retry() -> rs.pipeline:
    """Start pipeline, retrying after hardware_reset на VIDIOC_S_FMT errno=5.
    Waits for device presence first (boot/replug enumeration race)."""
    _wait_for_device(float(os.getenv("RS_DEVICE_WAIT_SEC", "30")))
    for attempt in range(HW_RESET_RETRIES + 1):
        pipeline = rs.pipeline()
        try:
            pipeline.start(_build_config())
            log.info("Pipeline started (attempt %d/%d)", attempt + 1, HW_RESET_RETRIES + 1)
            return pipeline
        except RuntimeError as e:
            if "VIDIOC_S_FMT" not in str(e) or attempt >= HW_RESET_RETRIES:
                raise
            log.warning("pipeline.start() failed (%s) — hardware_reset, retry %d/%d",
                        e, attempt + 1, HW_RESET_RETRIES)
            try:
                devs = list(rs.context().query_devices())
                if devs:
                    devs[0].hardware_reset()
                    time.sleep(6)
                else:
                    time.sleep(3)
            except Exception as he:
                log.warning("hardware_reset itself failed: %s", he)
                time.sleep(3)
    raise RuntimeError("pipeline.start exhausted retries")


# ── HTTP API request model (module-level for pydantic forward-ref resolution) ──

try:
    from pydantic import BaseModel

    class DepthQueryReq(BaseModel):
        """Sprint X3.2 — textroom round-trip query envelope.
        req_id is echoed back in response so relay can correlate the SSE broadcast.

        P1-CV-001: aligned=true reroutes к sample_aligned() so that (x, y) are
        interpreted в RGB color frame coords (default false = legacy depth-space)."""
        req_id: str
        x: float
        y: float
        aligned: bool = False
except ImportError:
    DepthQueryReq = None  # noqa — http server disabled когда pydantic unavailable


# ── Depth click-to-query sampler ───────────────────────────────────

# P1-OBS-001: lightweight per-sensor frame stats для /stats endpoint.
# Scraped by camera-page watchdog cycle → exported as Prometheus metric.
class _FpsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._window: dict = {}  # sensor → list of ts (rolling 5 sec)

    def record(self, sensor: str) -> None:
        now = time.time()
        with self._lock:
            buf = self._window.setdefault(sensor, [])
            buf.append(now)
            # Drop entries older than 5 sec
            cutoff = now - 5.0
            self._window[sensor] = [t for t in buf if t >= cutoff]

    def fps(self) -> dict:
        with self._lock:
            return {s: len(buf) / 5.0 for s, buf in self._window.items()}


_fps_tracker = _FpsTracker()


class CameraCalibration:
    """D435i RGB↔depth intrinsics + extrinsics — read once at startup.

    P1-CV-001: depth sensor and color sensor are physically offset на ~1.5cm
    (X axis). Same (x_pct, y_pct) клик в RGB video falls на different depth
    pixel — наивный sample() возвращает depth for a slightly shifted point.

    rs.intrinsics carries focal length + principal point at GIVEN resolution.
    rs.extrinsics carries 3×3 rotation + 3-vector translation depth→color.
    Distortion model ignored — D435 color обычно has near-zero coeffs.
    """
    def __init__(self, depth_intr, color_intr, d2c_extr):
        self.depth_intr = depth_intr
        self.color_intr = color_intr
        self.d2c_extr = d2c_extr
        self.color_w = color_intr.width
        self.color_h = color_intr.height
        self.depth_w = depth_intr.width
        self.depth_h = depth_intr.height
        # Vectorized scalars for numpy sampling
        self.fx_d = float(depth_intr.fx); self.fy_d = float(depth_intr.fy)
        self.cx_d = float(depth_intr.ppx); self.cy_d = float(depth_intr.ppy)
        self.fx_c = float(color_intr.fx); self.fy_c = float(color_intr.fy)
        self.cx_c = float(color_intr.ppx); self.cy_c = float(color_intr.ppy)
        self.R = np.asarray(d2c_extr.rotation, dtype=np.float32).reshape(3, 3)
        self.T = np.asarray(d2c_extr.translation, dtype=np.float32)


def _load_calibration(pipeline, color_w: int, color_h: int) -> Optional["CameraCalibration"]:
    """Query depth intrinsics из active pipeline + color intrinsics из device's
    color sensor profiles (color stream NOT enabled here — V4L2 owns it).
    Returns None gracefully on failure — caller falls back to unaligned sampling.
    """
    try:
        active = pipeline.get_active_profile()
        depth_profile = active.get_stream(rs.stream.depth).as_video_stream_profile()
        depth_intr = depth_profile.get_intrinsics()

        # Find color sensor on the same device (D435i has Stereo + RGB modules).
        device = active.get_device()
        color_sensor = None
        for s in device.sensors:
            for sp in s.get_stream_profiles():
                if sp.stream_type() == rs.stream.color:
                    color_sensor = s
                    break
            if color_sensor:
                break
        if color_sensor is None:
            log.warning("Calibration: no color sensor found — alignment disabled")
            return None

        # Pick color profile matching V4L2 capture resolution. Intrinsics scale
        # с resolution, so mismatched res → ~factor-of-2 sampling error.
        chosen = None
        for sp in color_sensor.get_stream_profiles():
            if sp.stream_type() != rs.stream.color:
                continue
            vsp = sp.as_video_stream_profile()
            if vsp.width() == color_w and vsp.height() == color_h:
                chosen = vsp
                break
        if chosen is None:
            log.warning("Calibration: no color profile %dx%d — alignment disabled",
                        color_w, color_h)
            return None

        color_intr = chosen.get_intrinsics()
        d2c_extr = depth_profile.get_extrinsics_to(chosen)
        baseline = float(np.linalg.norm(np.asarray(d2c_extr.translation)))
        log.info("Calibration: depth %dx%d (f=%.1f,%.1f) color %dx%d (f=%.1f,%.1f) baseline=%.4fm",
                 depth_intr.width, depth_intr.height, depth_intr.fx, depth_intr.fy,
                 color_intr.width, color_intr.height, color_intr.fx, color_intr.fy,
                 baseline)
        return CameraCalibration(depth_intr, color_intr, d2c_extr)
    except Exception as e:
        log.warning("Calibration load failed (%s) — alignment disabled", e)
        return None


class DepthSampler:
    """Lock-protected latest depth frame в metres + sampling API.

    Compatible с .55 realsense_mux protocol — routes/depth.py proxies
    `/api/v1/{cam_type}/depth?x=N&y=N` к /depth on this HTTP service.

    P1-CV-001: sample_aligned() interprets (x_pct, y_pct) as COLOR frame
    coords — reprojects every valid depth pixel forward through extrinsics
    + color intrinsics, returns min Z (foreground) among pixels landing
    on the clicked color pixel. Falls back to sample() when calibration
    unavailable.
    """
    def __init__(self, calibration: Optional["CameraCalibration"] = None):
        self._lock = threading.Lock()
        self._depth_m: Optional[np.ndarray] = None  # float32 H×W (depth-sensor native)
        self._shape: Optional[tuple] = None
        self._updated_at: float = 0.0
        # Phase 2.1: aligned depth array (depth reprojected к color frame coords).
        # Populated by Mux loop when ENABLE_COLOR + rs.align active. None means
        # fallback к CPU sample_aligned() reprojection path.
        self._aligned_depth_m: Optional[np.ndarray] = None  # float32 color_h×color_w
        self._aligned_shape: Optional[tuple] = None
        self._aligned_updated_at: float = 0.0
        # Lazy-align: timestamp последнего aligned query. Mux loop runs rs.align
        # only когда (now - this) < ALIGN_IDLE_SEC — avoids continuous CPU cost
        # когда aligned probe не used.
        self._last_aligned_query: float = 0.0
        self.calibration = calibration
        # Pixel-grid cache — meshgrid is ~5ms for 640×480, only compute once.
        self._j_grid: Optional[np.ndarray] = None
        self._i_grid: Optional[np.ndarray] = None

    def update(self, z16: np.ndarray, scale_m_per_unit: float) -> None:
        depth_m = z16.astype(np.float32) * scale_m_per_unit
        with self._lock:
            self._depth_m = depth_m
            self._shape = depth_m.shape[:2]
            self._updated_at = time.time()

    def update_aligned(self, z16_aligned: np.ndarray, scale_m_per_unit: float) -> None:
        """Phase 2.1 — rs.align pre-computed result. Same shape as color frame.
        sample_aligned() prefers это над CPU reprojection if available и fresh.
        """
        depth_m = z16_aligned.astype(np.float32) * scale_m_per_unit
        with self._lock:
            self._aligned_depth_m = depth_m
            self._aligned_shape = depth_m.shape[:2]
            self._aligned_updated_at = time.time()

    def aligned_recently(self) -> bool:
        """True если aligned query пришёл в последние ALIGN_IDLE_SEC. Mux loop
        gates rs.align на это — idle → skip align (CPU saving)."""
        with self._lock:
            last = self._last_aligned_query
        return (time.time() - last) < ALIGN_IDLE_SEC if last > 0 else False

    def sample(self, x_pct: float, y_pct: float) -> dict:
        """x/y as percentages (0..100). Returns dict со значениями metres + meta.
        Phase 1 (P1-REL-001): включает age_ms + stale flag so client может
        отличить "fresh" data от "USB stalled, ancient frame" silently passed."""
        with self._lock:
            arr = self._depth_m
            shape = self._shape
            ts = self._updated_at
        if arr is None or shape is None:
            raise RuntimeError("no depth frame yet")
        age_ms = int((time.time() - ts) * 1000) if ts > 0 else -1
        stale = age_ms >= DEPTH_STALE_MS
        H, W = shape
        xn = max(0.0, min(1.0, x_pct / 100.0))
        yn = max(0.0, min(1.0, y_pct / 100.0))
        j = int(round(xn * (W - 1)))
        i = int(round(yn * (H - 1)))
        depth_val = float(arr[i, j])
        return {"depth_m": depth_val, "i": i, "j": j, "w": W, "h": H,
                "ts": ts, "age_ms": age_ms, "stale": stale}

    def sample_aligned(self, x_pct: float, y_pct: float) -> dict:
        """P1-CV-001: aligned click-to-depth.

        x_pct, y_pct are interpreted в COLOR frame coords (0..100%).

        Phase 2.1: if rs.align pre-computed aligned depth array available и fresh,
        use direct lookup (O(1), no CPU reprojection). Falls back к:
          • CPU reprojection (legacy P1-CV-001 path) if calibration loaded
          • sample() native depth if neither available
        """
        # Record query timestamp so Mux loop starts/continues rs.align (lazy gate).
        now = time.time()
        with self._lock:
            self._last_aligned_query = now
            ad = self._aligned_depth_m
            ashape = self._aligned_shape
            ats = self._aligned_updated_at
        # Fast path только если aligned array fresh — после idle первый query
        # видит stale/no array, падает к CPU reproject; следующий tick repopulates.
        if ad is not None and ashape is not None and (now - ats) < (ALIGN_IDLE_SEC + 1.0):
            age_ms = int((now - ats) * 1000) if ats > 0 else -1
            stale = age_ms >= DEPTH_STALE_MS
            H, W = ashape
            xn = max(0.0, min(1.0, x_pct / 100.0))
            yn = max(0.0, min(1.0, y_pct / 100.0))
            j = int(round(xn * (W - 1)))
            i = int(round(yn * (H - 1)))
            depth_val = float(ad[i, j])
            return {"depth_m": depth_val, "i": i, "j": j, "w": W, "h": H,
                    "ts": ats, "age_ms": age_ms, "stale": stale,
                    "aligned": True, "reason": "rs_align_precomputed"}

        cal = self.calibration
        if cal is None:
            r = self.sample(x_pct, y_pct)
            r["aligned"] = False
            r["reason"] = "no_calibration"
            return r

        with self._lock:
            arr = self._depth_m
            shape = self._shape
            ts = self._updated_at
        if arr is None or shape is None:
            raise RuntimeError("no depth frame yet")

        age_ms = int((time.time() - ts) * 1000) if ts > 0 else -1
        stale = age_ms >= DEPTH_STALE_MS
        H_d, W_d = shape

        # Target color pixel (xc, yc) — fractional, kept as float for sub-pixel
        # matching tolerance.
        xc = max(0.0, min(1.0, x_pct / 100.0)) * (cal.color_w - 1)
        yc = max(0.0, min(1.0, y_pct / 100.0)) * (cal.color_h - 1)

        # Lazy meshgrid cache (one-shot per resolution).
        if (self._j_grid is None or self._i_grid is None
                or self._j_grid.shape != (H_d, W_d)):
            j_idx, i_idx = np.meshgrid(
                np.arange(W_d, dtype=np.float32),
                np.arange(H_d, dtype=np.float32),
            )
            self._j_grid = j_idx
            self._i_grid = i_idx

        # Mask: depth values ≥ 1cm (below = invalid / no return).
        valid = arr > 0.01
        if not np.any(valid):
            return {"depth_m": 0.0, "i": -1, "j": -1,
                    "w": cal.color_w, "h": cal.color_h,
                    "ts": ts, "age_ms": age_ms, "stale": stale,
                    "aligned": True, "reason": "no_valid_depth"}

        z = arr[valid]
        j_d = self._j_grid[valid]
        i_d = self._i_grid[valid]

        # Deproject к 3D point in depth-camera frame (pinhole, no distortion).
        x_d = (j_d - cal.cx_d) * z / cal.fx_d
        y_d = (i_d - cal.cy_d) * z / cal.fy_d
        P_d = np.stack([x_d, y_d, z], axis=0)  # 3×N

        # Apply depth→color extrinsics (R: 3×3, T: 3).
        P_c = cal.R @ P_d + cal.T[:, None]
        z_c = P_c[2]
        front = z_c > 0.05
        if not np.any(front):
            return {"depth_m": 0.0, "i": -1, "j": -1,
                    "w": cal.color_w, "h": cal.color_h,
                    "ts": ts, "age_ms": age_ms, "stale": stale,
                    "aligned": True, "reason": "no_depth_in_front_of_color"}

        x_c = P_c[0][front]; y_c = P_c[1][front]; z_cf = z_c[front]
        uc_p = cal.fx_c * x_c / z_cf + cal.cx_c
        vc_p = cal.fy_c * y_c / z_cf + cal.cy_c

        # Match window: ±0.6px gives ~1px tolerance, more forgiving than ±0.5
        # to absorb sub-pixel jitter без missing every click.
        match = (np.abs(uc_p - xc) < 0.6) & (np.abs(vc_p - yc) < 0.6)
        if np.any(match):
            depth_val = float(np.min(z_cf[match]))
            reason = "ok"
        else:
            # Nearest-neighbor fallback (no depth pixel projects exactly to
            # clicked location — pick closest in color pixel space).
            dists = (uc_p - xc) ** 2 + (vc_p - yc) ** 2
            idx = int(np.argmin(dists))
            depth_val = float(z_cf[idx])
            reason = "nearest_neighbor"

        return {"depth_m": depth_val,
                "i": int(round(yc)), "j": int(round(xc)),
                "w": cal.color_w, "h": cal.color_h,
                "ts": ts, "age_ms": age_ms, "stale": stale,
                "aligned": True, "reason": reason}


def _start_http_server(sampler: DepthSampler, host: str, port: int) -> None:
    """Start FastAPI uvicorn в a daemon thread. routes/depth.py expects
    /depth?x=N&y=N → 200 {x, y, depth} (depth в metres).
    """
    try:
        from fastapi import FastAPI, HTTPException
        import uvicorn
    except ImportError as e:
        log.warning("FastAPI/uvicorn unavailable — depth HTTP API disabled (%s)", e)
        return

    app = FastAPI(title="realsense-mux", docs_url=None, redoc_url=None)

    @app.get("/depth")
    async def get_depth(x: float, y: float, aligned: bool = False):
        """Click-to-depth. (x, y) interpreted as sensor-native percentages.

        Rotation contract: mux always samples RAW sensor pixels (no rotation
        logic here). Browser is responsible для inversing any CSS + ffmpeg
        rotation перед sending coords. См. depth_features.js viewportToFrame().

        aligned=true (P1-CV-001): interprets x/y в RGB color frame coords и
        reprojects depth via extrinsics к returned depth at clicked color pixel.
        """
        try:
            s = sampler.sample_aligned(x, y) if aligned else sampler.sample(x, y)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        out = {"type": "depth", "x": x, "y": y, "depth": s["depth_m"],
               "age_ms": s["age_ms"], "stale": s["stale"]}
        if aligned:
            out["aligned"] = s.get("aligned", False)
            out["reason"] = s.get("reason")
        return out

    @app.post("/depth_query")
    async def depth_query(body: DepthQueryReq):
        """Textroom path (browser → Janus textroom → relay → here). Same
        sensor-native coord contract as /depth."""
        try:
            s = (sampler.sample_aligned(body.x, body.y) if body.aligned
                 else sampler.sample(body.x, body.y))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        out = {
            "type": "depth_result",
            "req_id": body.req_id,
            "x": body.x, "y": body.y,
            "depth": s["depth_m"],
            "age_ms": s["age_ms"],
            "stale": s["stale"],
        }
        if body.aligned:
            out["aligned"] = s.get("aligned", False)
            out["reason"] = s.get("reason")
        return out

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/stats")
    async def stats():
        """P1-OBS-001: per-sensor FPS из rolling 5-sec window. Scraped
        by camera-page watchdog → exported as camstack_mux_input_fps{sensor}."""
        return {"fps": _fps_tracker.fps()}

    def _serve():
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    access_log=False)

    th = threading.Thread(target=_serve, daemon=True, name="rs-mux-http")
    th.start()
    log.info("HTTP depth API listening на http://%s:%d/depth", host, port)


# ── Main loop ──────────────────────────────────────────────────────

class Mux:
    def __init__(self):
        self.running = True
        self.pipeline: Optional[rs.pipeline] = None
        self.colorizer = rs.colorizer()
        self.fifo_depth = FifoWriter(DEPTH_FIFO, "depth")
        self.fifo_ir1   = FifoWriter(IR1_FIFO,   "ir1")
        self.fifo_ir2   = FifoWriter(IR2_FIFO,   "ir2")
        # Phase 2.1: color FIFO + rs.align — only created when ENABLE_COLOR.
        # Else stay None (no FIFO created на disk → consumer service won't try).
        self.fifo_color: Optional[FifoWriter] = (
            FifoWriter(COLOR_FIFO, "color") if ENABLE_COLOR else None
        )
        self.align: Optional[rs.align] = (
            rs.align(rs.stream.color) if ENABLE_COLOR else None
        )
        self.depth_sampler = DepthSampler()
        self.depth_scale: float = 0.001  # D435 default; overridden after pipeline.start

    def stop(self, *_):
        self.running = False
        log.info("SIGTERM received — initiating shutdown")

    def run(self):
        signal.signal(signal.SIGINT,  self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        self.pipeline = _start_pipeline_with_retry()
        log.info("Streaming: depth=%dx%d@%d, ir=%dx%d@%d, fifo_dir=%s",
                 DEPTH_WIDTH, DEPTH_HEIGHT, DEPTH_FPS,
                 IR_WIDTH,    IR_HEIGHT,    IR_FPS, FIFO_DIR)
        if ENABLE_COLOR:
            log.info("Streaming: color=%dx%d@%d (rs.align enabled)",
                     COLOR_WIDTH, COLOR_HEIGHT, COLOR_FPS)

        # Read depth scale from sensor (e.g., 0.001 m/unit для D435).
        try:
            profile = self.pipeline.get_active_profile()
            depth_sensor = profile.get_device().first_depth_sensor()
            self.depth_scale = float(depth_sensor.get_depth_scale())
            log.info("Depth scale: %g m/unit", self.depth_scale)
        except Exception as e:
            log.warning("Could not read depth_scale (using default 0.001): %s", e)

        # P1-CV-001: load RGB↔depth calibration so sample_aligned() works.
        # Color stream НЕ added to pipeline (V4L2 owns it) — intrinsics
        # обращаемся через color_sensor.get_stream_profiles().
        self.depth_sampler.calibration = _load_calibration(
            self.pipeline, COLOR_WIDTH, COLOR_HEIGHT
        )

        # Start HTTP API thread for click-to-depth queries.
        _start_http_server(self.depth_sampler, HTTP_BIND_HOST, HTTP_PORT)

        consec_timeouts = 0
        last_stats = time.time()
        frames_depth = frames_ir1 = frames_ir2 = frames_color = 0

        try:
            while self.running:
                try:
                    frames = self.pipeline.wait_for_frames(timeout_ms=FRAME_TIMEOUT_MS)
                    consec_timeouts = 0
                except RuntimeError as e:
                    consec_timeouts += 1
                    log.warning("wait_for_frames timeout %d/%d (%s)",
                                consec_timeouts, MAX_CONSEC_TIMEOUTS, e)
                    if consec_timeouts >= MAX_CONSEC_TIMEOUTS:
                        log.error("Camera unresponsive — exiting for systemd restart")
                        return 2
                    continue

                # depth → (1) raw z16 для click-to-depth sampler; (2) colorize → rgb24 → FIFO
                df = frames.get_depth_frame()
                if df:
                    frames_depth += 1
                    _fps_tracker.record("depth")
                    z16 = np.asanyarray(df.get_data())  # H×W uint16
                    # Sampler keeps the raw depth для HTTP /depth queries.
                    # Stored as float32 metres = z16 × depth_scale.
                    self.depth_sampler.update(z16, self.depth_scale)
                    # Visual stream: colorize → rgb24 → FIFO для ffmpeg encoder.
                    # NOTE: mux DOES NOT rotate FIFO bytes (would require swapping
                    # WIDTH/HEIGHT в rs-stream.tuning.env для 90°/270° или break
                    # ffmpeg decode). Visual rotation handled via CSS на frontend
                    # (supports arbitrary angle без dimensions changes).
                    # /depth и /depth_query endpoints pre-flip incoming coords so
                    # the same RS_OUTPUT_ROTATION_DEG keeps math consistent.
                    cz = self.colorizer.process(df)
                    img = np.ascontiguousarray(
                        np.asanyarray(cz.get_data()).astype(np.uint8))  # H×W×3
                    self.fifo_depth.write(img.tobytes())

                # ir1 — rotation NOT applied: ir viewers use color_view.html which
                # inherits player.css --video-rotation: 180deg; mux-rotating IR
                # here would result в double-rotation (visual upside-down). Когда
                # IR-specific viewer template lands, can rotate here по аналогии.
                ir1 = frames.get_infrared_frame(1)
                if ir1:
                    frames_ir1 += 1
                    _fps_tracker.record("ir1")
                    arr = np.ascontiguousarray(np.asanyarray(ir1.get_data()))
                    self.fifo_ir1.write(arr.tobytes())

                # ir2 — see note выше.
                ir2 = frames.get_infrared_frame(2)
                if ir2:
                    frames_ir2 += 1
                    _fps_tracker.record("ir2")
                    arr = np.ascontiguousarray(np.asanyarray(ir2.get_data()))
                    self.fifo_ir2.write(arr.tobytes())

                # color (Phase 2.1, gated) — rgb8 frames → /run/realsense/color.fifo
                # consumed by rs-stream@color.service. Also produces aligned-depth
                # array for sample_aligned() pre-computed path.
                if ENABLE_COLOR and self.fifo_color is not None:
                    cf = frames.get_color_frame()
                    if cf:
                        frames_color += 1
                        _fps_tracker.record("color")
                        arr = np.ascontiguousarray(np.asanyarray(cf.get_data()))
                        self.fifo_color.write(arr.tobytes())
                    # rs.align: produces depth frame в color coord space —
                    # sample_aligned() reads pre-computed array. LAZY: only run
                    # когда aligned probe queried recently (ALIGN_IDLE_SEC) —
                    # full-frame reproject costs ~half mux CPU, no reason к pay it
                    # continuously когда nobody clicks aligned.
                    if self.align is not None and self.depth_sampler.aligned_recently():
                        try:
                            aligned = self.align.process(frames)
                            adf = aligned.get_depth_frame()
                            if adf:
                                az16 = np.asanyarray(adf.get_data())
                                self.depth_sampler.update_aligned(az16, self.depth_scale)
                        except Exception as e:
                            log.debug("align process error: %s", e)

                now = time.time()
                if now - last_stats >= STATS_INTERVAL:
                    dt = now - last_stats
                    color_str = ""
                    if ENABLE_COLOR and self.fifo_color is not None:
                        color_str = f" | color: {frames_color / dt:.1f} fps ({self.fifo_color.drops} drops)"
                        self.fifo_color.drops = 0
                    log.info(
                        "[stats] depth: %.1f fps (%d drops) | ir1: %.1f fps (%d drops) | ir2: %.1f fps (%d drops)%s",
                        frames_depth / dt, self.fifo_depth.drops,
                        frames_ir1   / dt, self.fifo_ir1.drops,
                        frames_ir2   / dt, self.fifo_ir2.drops,
                        color_str,
                    )
                    frames_depth = frames_ir1 = frames_ir2 = frames_color = 0
                    self.fifo_depth.drops = self.fifo_ir1.drops = self.fifo_ir2.drops = 0
                    last_stats = now
        finally:
            log.info("Shutting down pipeline и FIFOs")
            try:
                if self.pipeline:
                    self.pipeline.stop()
            except Exception as e:
                log.warning("pipeline.stop() error: %s", e)
            self.fifo_depth.close()
            self.fifo_ir1.close()
            self.fifo_ir2.close()
            if self.fifo_color is not None:
                self.fifo_color.close()
        return 0


def main():
    return Mux().run()


if __name__ == "__main__":
    sys.exit(main())
