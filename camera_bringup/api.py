"""Public API of L0 (USB / kernel / udev / V4L2 layer).

Это **единственный** интерфейс который должны использовать вышестоящие
слои (L1 network, L2 encoder, ...) и future agent.

Не использовать `checks/*` или `fixers/*` напрямую — это internal реализация.

См. [CONTRACT.md](../CONTRACT.md) для формальной спецификации.

## Quick start для L2+

```python
from camera_bringup import L0, LayerStatus

# 1. Готова ли L0 для использования?
if not L0.is_ready():
    raise RuntimeError(f"L0 not ready: {L0.requires_human()}")

# 2. Получить параметры стрима для encoder'а
profile = L0.stream_profile()
# → StreamProfile(device_path='/dev/cam-rgb', pixel_format='YUYV',
#                  width=640, height=480, fps=15)
spawn_ffmpeg(device=profile.device_path, **profile.encoder_kwargs())

# 3. Calibration для 3D CV pipeline
calib = L0.calibration("color")
# → CalibrationIntrinsics(fx=1356.88, fy=1356.64, ppx=959.13, ppy=559.20, ...)
camera_matrix = np.array([[calib.fx, 0, calib.ppx],
                          [0, calib.fy, calib.ppy],
                          [0, 0, 1]])

# 4. Granular guarantees
if not L0.guarantees().USB_POWER_LOCKED_ON:
    log.warning("USB autosuspend may interrupt long sessions")

# 5. Snapshot для UI/log/agent
snap = L0.snapshot()
# → Snapshot(status=HEALTHY, identity=Identity(serial=...), ...)
json.dump(snap.to_dict(), file)

# 6. Auto-recovery
result = L0.attempt_recovery()
if result.failed_fixers:
    log.error("recovery failed: %s", result.failed_fixers)
```

## Statuses

LayerStatus enum:
  - `HEALTHY` — всё OK, можно работать
  - `DRIFTED` — есть auto-fixable drift, попробуй `attempt_recovery()`
  - `DEGRADED` — работает но не идеально (e.g. USB2 cable); смотри `requires_human()`
  - `SAFE` — operator quarantined; apply blocked, verify работает
  - `BROKEN` — есть unfixable FAIL; нужно human
  - `UNKNOWN` — ошибка в check'е; не доверяй
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from threading import RLock
from typing import Any

from camera_bringup.check import CheckResult, Status, safe_run
from camera_bringup.checks import ALL_CHECKS
from camera_bringup.fixer import FixerResult, FixerStatus, run_fixer
from camera_bringup.fixers import ALL_FIXERS

__all__ = [
    "ALL_GUARANTEES",
    # Const tables
    "GUARANTEES",
    "HUMAN_REQUIRED",
    # Main facade
    "L0",
    "CalibrationIntrinsics",
    "Guarantees",
    # Typed return values
    "Identity",
    # Status enum
    "LayerStatus",
    "RecoveryResult",
    "Snapshot",
    "StreamProfile",
]


# ── TTL cache для результатов checks ─────────────────────────────────

_STATUS_CACHE_TTL_S = float(os.environ.get("CAMERA_BRINGUP_STATUS_TTL_S", "5.0"))


class _CheckResultCache:
    """Thread-safe TTL cache. Stores last full check pass."""

    def __init__(self, ttl_s: float):
        self._ttl_s = ttl_s
        self._results: list[CheckResult] | None = None
        self._ctx: dict[str, Any] | None = None
        self._ts: float = 0.0
        self._lock = RLock()

    def get(self) -> tuple | None:
        with self._lock:
            if self._results is None:
                return None
            if time.monotonic() - self._ts > self._ttl_s:
                return None
            return self._results, self._ctx

    def set(self, results: list[CheckResult], ctx: dict[str, Any]) -> None:
        with self._lock:
            self._results = results
            self._ctx = ctx
            self._ts = time.monotonic()

    def invalidate(self) -> None:
        with self._lock:
            self._results = None
            self._ctx = None
            self._ts = 0.0


_cache = _CheckResultCache(_STATUS_CACHE_TTL_S)


# ── Layer status enum ─────────────────────────────────────────────────

class LayerStatus(str, Enum):
    HEALTHY = "healthy"
    """Все checks OK. Все postconditions выполнены."""

    DRIFTED = "drifted"
    """Есть WARN или FAIL, но хотя бы один auto-fixable через attempt_recovery()."""

    DEGRADED = "degraded"
    """Есть WARN'ы которые НЕ fixable автоматически (например USB2 vs USB3).
    Стрим работает, но не идеально. Требует human intervention для улучшения."""

    SAFE = "safe"
    """F-Isolation: компонент явно quarantined (operator или automated FDIR).
    Apply заблокирован. Verify работает в read-only mode. См. ECSS-Q-ST-30."""

    BROKEN = "broken"
    """Есть FAIL без auto-fix. Стрим работать НЕ будет. Требует human."""

    UNKNOWN = "unknown"
    """ERROR в checks. Состояние слоя неопределимо."""


# ── Typed return dataclasses ──────────────────────────────────────────

@dataclass(frozen=True)
class Identity:
    """Identity подключённой камеры (RealSense)."""
    vendor_id: str
    product_id: str
    serial: str | None
    firmware: str | None
    product_name: str | None
    product_line: str | None
    usb_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationIntrinsics:
    """Factory intrinsics одного sensor (color/depth/IR).
    Для CV pipeline (undistortion, 3D back-projection).

    Build camera matrix:
        K = [[fx, 0, ppx], [0, fy, ppy], [0, 0, 1]]
    """
    sensor: str           # e.g. "RGB Camera"
    width: int
    height: int
    fx: float             # focal length x (pixels)
    fy: float             # focal length y (pixels)
    ppx: float            # principal point x (pixels)
    ppy: float            # principal point y (pixels)
    model: str            # distortion model name ("brown_conrady", "inverse_brown_conrady")
    coeffs: tuple[float, ...]   # 5 distortion coefficients

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_camera_matrix(self) -> list[list[float]]:
        """3x3 camera intrinsics matrix K (for cv2.undistort, projection, etc.)."""
        return [
            [self.fx, 0.0, self.ppx],
            [0.0, self.fy, self.ppy],
            [0.0, 0.0, 1.0],
        ]


@dataclass(frozen=True)
class StreamProfile:
    """Параметры стрима для encoder'а (L2)."""
    device_path: str      # e.g. "/dev/cam-rgb"
    pixel_format: str     # e.g. "YUYV"
    width: int
    height: int
    fps: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def encoder_kwargs(self) -> dict[str, Any]:
        """Convenience: kwargs для ffmpeg/gstreamer-like APIs."""
        return {
            "video_size": f"{self.width}x{self.height}",
            "framerate": self.fps,
            "input_format": self.pixel_format.lower(),
        }


@dataclass(frozen=True)
class Guarantees:
    """Все 12 L0 guarantees как typed booleans.

    Attribute access вместо dict subscript:
        if not L0.guarantees().USB_POWER_LOCKED_ON:
            ...
    """
    CAMERA_PRESENT: bool
    SINGLE_D435I: bool
    USB_POWER_LOCKED_ON: bool
    UVCVIDEO_QUIRKS_OK: bool
    UDEV_RULE_INSTALLED: bool
    DEV_CAM_RGB_VALID: bool
    V4L2_CAPTURE_READY: bool
    FIRMWARE_NOT_TOO_OLD: bool
    BANDWIDTH_FITS_USB: bool
    RESET_TOOLS_AVAILABLE: bool
    ENCODER_PREREQS_OK: bool
    IDENTITY_MATCHES_BASELINE: bool

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    def __getitem__(self, key: str) -> bool:
        """Dict-style access для backward compat."""
        return getattr(self, key)

    def all_satisfied(self) -> bool:
        return all(self.to_dict().values())

    def unsatisfied(self) -> list[str]:
        return [k for k, v in self.to_dict().items() if not v]


@dataclass(frozen=True)
class Snapshot:
    """One-shot snapshot всего L0 для UI/log/agent."""
    layer: str
    status: LayerStatus
    checks_total: int
    status_counts: dict[str, int]
    identity: Identity | None
    baseline_serial: str | None
    requires_human: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        if self.identity:
            d["identity"] = self.identity.to_dict()
        return d

    def __getitem__(self, key: str) -> Any:
        """Dict-style access для backward compat."""
        return self.to_dict()[key]


# ── GUARANTEES table (mapping guarantee → check + accepted statuses) ─

GUARANTEES: dict[str, tuple] = {
    "CAMERA_PRESENT":          ("usb_enumerate", {Status.OK, Status.WARN}),
    "SINGLE_D435I":            ("usb_enumerate", {Status.OK, Status.WARN}),
    "USB_POWER_LOCKED_ON":     ("usb_power",     {Status.OK}),
    "UVCVIDEO_QUIRKS_OK":      ("uvcvideo",      {Status.OK}),
    "UDEV_RULE_INSTALLED":     ("udev",          {Status.OK}),
    "DEV_CAM_RGB_VALID":       ("dev_symlinks",  {Status.OK}),
    "V4L2_CAPTURE_READY":      ("v4l2",          {Status.OK}),
    "FIRMWARE_NOT_TOO_OLD":    ("firmware",      {Status.OK}),
    "BANDWIDTH_FITS_USB":      ("bandwidth",     {Status.OK, Status.WARN}),
    "RESET_TOOLS_AVAILABLE":   ("reset_tools",   {Status.OK}),
    "ENCODER_PREREQS_OK":      ("smoke",         {Status.OK}),
    "IDENTITY_MATCHES_BASELINE": ("fingerprint", {Status.OK, Status.WARN}),
}

ALL_GUARANTEES: list[str] = list(GUARANTEES.keys())


# ── Escalation matrix ────────────────────────────────────────────────

HUMAN_REQUIRED: dict[str, dict[Status, str]] = {
    "usb_enumerate": {
        Status.FAIL: "camera physically missing OR multiple D435i found (need physical action)",
        Status.WARN: "USB2 cable detected — replace with USB3 cable to unlock higher bandwidth (optional)",
    },
    "fingerprint": {
        Status.FAIL: "camera identity mismatch — physical swap detected, confirm and re-baseline",
    },
    "firmware": {
        Status.WARN: "firmware below minimum — needs rs-fw-update (manual)",
    },
    "bandwidth": {
        Status.WARN: "stream profile near USB bandwidth limit — reduce fps/resolution or switch to USB3",
        Status.FAIL: "stream profile exceeds USB bandwidth — reduce fps/resolution",
    },
}


# ── Recovery result ──────────────────────────────────────────────────

@dataclass
class RecoveryResult:
    attempted: bool
    applied_fixers: list[str] = field(default_factory=list)
    skipped_fixers: list[str] = field(default_factory=list)
    failed_fixers: list[str] = field(default_factory=list)
    unfixed_fixers: list[str] = field(default_factory=list)
    remaining_issues: list[str] = field(default_factory=list)
    requires_human: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fully_recovered(self) -> bool:
        return self.attempted and not self.remaining_issues

    @property
    def needs_attention(self) -> bool:
        """True если что-то failed/unfixed или требует human."""
        return bool(self.failed_fixers or self.unfixed_fixers or self.requires_human)


# ── L0 facade ────────────────────────────────────────────────────────

class L0:
    """L0 — USB / kernel / udev / V4L2 layer.

    Singleton (один физический USB-узел = один L0 instance в process).
    Все методы статические.

    ## Status check pattern

    ```python
    from camera_bringup import L0, LayerStatus

    match L0.status():
        case LayerStatus.HEALTHY:
            start_pipeline()
        case LayerStatus.DRIFTED:
            L0.attempt_recovery()
        case LayerStatus.DEGRADED:
            log.warn("functional but suboptimal: %s", L0.requires_human())
            start_pipeline()
        case LayerStatus.SAFE:
            log.info("L0 quarantined, not starting")
        case _:  # BROKEN / UNKNOWN
            raise CameraNotReady(L0.requires_human())
    ```

    ## Convenience shortcuts

    ```python
    if L0.is_ready():            # HEALTHY or DEGRADED
        ...
    if L0.is_usable():           # anything except BROKEN/UNKNOWN/SAFE
        ...
    profile = L0.stream_profile()  # для encoder'а
    cal = L0.calibration("color")  # для CV pipeline
    ```

    ## Diagnostics

    ```python
    snap = L0.snapshot()         # full state snapshot
    json.dump(snap.to_dict(), file)
    ```
    """

    LAYER_NAME = "L0_usb_kernel_udev_v4l2"

    # ── Internal: check runner with cache ────────────────────────

    @staticmethod
    def _run_all_checks(*, use_cache: bool = True) -> list[CheckResult]:
        if use_cache:
            cached = _cache.get()
            if cached is not None:
                return cached[0]
        ctx: dict = {}
        results = [safe_run(name, fn, ctx) for name, fn in ALL_CHECKS]
        _cache.set(results, ctx)
        return results

    @staticmethod
    def _run_all_checks_with_ctx(*, use_cache: bool = True) -> tuple:
        if use_cache:
            cached = _cache.get()
            if cached is not None:
                return cached
        ctx: dict = {}
        results = [safe_run(name, fn, ctx) for name, fn in ALL_CHECKS]
        _cache.set(results, ctx)
        return results, ctx

    @staticmethod
    def invalidate_cache() -> None:
        """Сбросить cache. Вызывай после external mutations (например после
        manual `apply` через CLI чтобы programmatic API сразу видел изменения)."""
        _cache.invalidate()

    # ── Status ────────────────────────────────────────────────────

    @staticmethod
    def status() -> LayerStatus:
        """Текущий статус слоя.

        Returns:
            LayerStatus enum. См. priorities в class docstring.
        """
        from camera_bringup.safe_mode import is_safe_mode
        if is_safe_mode():
            results = L0._run_all_checks()
            if any(r.status == Status.ERROR for r in results):
                return LayerStatus.UNKNOWN
            return LayerStatus.SAFE

        results = L0._run_all_checks()
        return L0._derive_status(results)

    @staticmethod
    def is_ready() -> bool:
        """True если status == HEALTHY или DEGRADED (стрим может работать).

        Convenience для quick check в L2+:
            if L0.is_ready():
                spawn_encoder()
        """
        return L0.status() in (LayerStatus.HEALTHY, LayerStatus.DEGRADED)

    @staticmethod
    def is_usable() -> bool:
        """True для всех состояний кроме BROKEN/UNKNOWN/SAFE.
        Включает DRIFTED (камера ещё работает, но скоро нужен apply).
        """
        return L0.status() in (
            LayerStatus.HEALTHY,
            LayerStatus.DRIFTED,
            LayerStatus.DEGRADED,
        )

    @staticmethod
    def _derive_status(results: list[CheckResult]) -> LayerStatus:
        if any(r.status == Status.ERROR for r in results):
            return LayerStatus.UNKNOWN

        fails = [r for r in results if r.status == Status.FAIL]
        if fails:
            has_fixer_for_fail = any(r.name in ALL_FIXERS for r in fails)
            if not has_fixer_for_fail:
                return LayerStatus.BROKEN
            return LayerStatus.DRIFTED

        warns = [r for r in results if r.status == Status.WARN]
        if warns:
            has_fixer_for_warn = any(r.name in ALL_FIXERS for r in warns)
            if has_fixer_for_warn:
                return LayerStatus.DRIFTED
            return LayerStatus.DEGRADED

        return LayerStatus.HEALTHY

    # ── Identity ──────────────────────────────────────────────────

    @staticmethod
    def identity() -> Identity | None:
        """Текущая identity камеры (через pyrealsense2).

        Returns:
            Identity dataclass или None если камеры нет / pyrealsense2 недоступен.
        """
        from camera_bringup.realsense_query import primary_device
        from camera_bringup.spec import ACTIVE_INSTANCE
        d = primary_device()
        if d is None:
            return None
        return Identity(
            vendor_id=ACTIVE_INSTANCE.hardware.usb_vendor_id,
            product_id=ACTIVE_INSTANCE.hardware.usb_product_id,
            serial=d.get("serial"),
            firmware=d.get("firmware"),
            product_name=d.get("name"),
            product_line=d.get("product_line"),
            usb_type=d.get("usb_type"),
        )

    @staticmethod
    def baseline_identity() -> dict[str, Any] | None:
        """Полное содержимое baseline fingerprint (raw dict).

        Returns:
            Полный fingerprint.json как dict (включая calibration, history,
            HMAC signature). None если baseline ещё не создан.
        """
        import json
        from pathlib import Path

        from camera_bringup.spec import FINGERPRINT_PATH
        path = Path(FINGERPRINT_PATH)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    # ── Stream profile (для L2 encoder) ──────────────────────────

    @staticmethod
    def stream_profile() -> StreamProfile:
        """Параметры стрима для encoder'а L2.

        Returns:
            StreamProfile с device_path / format / dimensions / fps.

        Example:
            profile = L0.stream_profile()
            subprocess.run([
                "ffmpeg", "-f", "v4l2",
                "-input_format", profile.pixel_format.lower(),
                "-video_size", f"{profile.width}x{profile.height}",
                "-framerate", str(profile.fps),
                "-i", profile.device_path,
                ...
            ])
        """
        from camera_bringup.spec import ACTIVE_INSTANCE, DEV_SYMLINK
        s = ACTIVE_INSTANCE.stream
        return StreamProfile(
            device_path=DEV_SYMLINK,
            pixel_format=s.pixel_format,
            width=s.width,
            height=s.height,
            fps=s.fps,
        )

    # ── Calibration (для L2+ CV pipeline) ────────────────────────

    @staticmethod
    def calibration(stream: str = "color") -> CalibrationIntrinsics | None:
        """Factory intrinsics из fingerprint baseline для CV pipeline.

        Args:
            stream: "color" | "depth" | "infrared"

        Returns:
            CalibrationIntrinsics или None если baseline нет, или нет данных
            для этого stream type.

        Example:
            cal = L0.calibration("color")
            K = np.array(cal.to_camera_matrix())  # 3x3 intrinsics matrix
            undistorted = cv2.undistort(img, K, cal.coeffs)
        """
        baseline = L0.baseline_identity()
        if baseline is None:
            return None
        cal_data = baseline.get("calibration", {}).get(stream)
        if not cal_data:
            return None
        return CalibrationIntrinsics(
            sensor=cal_data.get("sensor", ""),
            width=cal_data.get("width", 0),
            height=cal_data.get("height", 0),
            fx=float(cal_data.get("fx", 0)),
            fy=float(cal_data.get("fy", 0)),
            ppx=float(cal_data.get("ppx", 0)),
            ppy=float(cal_data.get("ppy", 0)),
            model=cal_data.get("model", ""),
            coeffs=tuple(cal_data.get("coeffs", ())),
        )

    @staticmethod
    def available_calibrations() -> list[str]:
        """Список stream types для которых есть calibration в baseline.
        Обычно ['color', 'depth', 'infrared'] для D435i.
        """
        baseline = L0.baseline_identity()
        if baseline is None:
            return []
        return list(baseline.get("calibration", {}).keys())

    # ── Postconditions / guarantees ──────────────────────────────

    @staticmethod
    def guarantees() -> Guarantees:
        """Все 12 L0 guarantees как typed booleans.

        Returns:
            Guarantees dataclass — attribute access (preferred):
                pc = L0.guarantees()
                if not pc.USB_POWER_LOCKED_ON: ...

            Также поддерживает dict-style для backward compat:
                pc["CAMERA_PRESENT"]
        """
        results = L0._run_all_checks()
        check_status: dict[str, Status] = {r.name: r.status for r in results}
        return Guarantees(**{
            guarantee: check_status.get(check_name) in accepted
            for guarantee, (check_name, accepted) in GUARANTEES.items()
        })

    # Backward compat alias
    @staticmethod
    def postconditions() -> Guarantees:
        """Alias для L0.guarantees() (старое имя)."""
        return L0.guarantees()

    # ── Recovery ──────────────────────────────────────────────────

    @staticmethod
    def attempt_recovery(*, dry_run: bool = False) -> RecoveryResult:
        """Запустить все доступные fixers idempotently.

        Args:
            dry_run: True = только plan, не выполнять.

        Returns:
            RecoveryResult с applied/skipped/failed/unfixed fixer lists.

        Note:
            Блокируется когда L0 в SAFE mode (return RecoveryResult с
            requires_human указанием выйти из SAFE).
        """
        from camera_bringup.safe_mode import is_safe_mode, safe_mode_info
        if is_safe_mode() and not dry_run:
            info = safe_mode_info() or {}
            return RecoveryResult(
                attempted=False,
                remaining_issues=[
                    f"L0 in SAFE mode since {info.get('ts', '?')} "
                    f"(reason: {info.get('reason', '?')}); apply blocked"
                ],
                requires_human=["exit_safe_mode() to allow apply"],
            )

        L0.invalidate_cache()
        _results, ctx = L0._run_all_checks_with_ctx(use_cache=False)

        fixer_results: list[FixerResult] = []
        for name, cls in ALL_FIXERS.items():
            from camera_bringup.checks import get_check
            check_fn = get_check(name)
            fixer = cls()
            fr = run_fixer(fixer, check_fn, ctx, dry_run=dry_run)
            fixer_results.append(fr)

        result = RecoveryResult(attempted=not dry_run)
        for fr in fixer_results:
            if fr.status == FixerStatus.SKIPPED:
                result.skipped_fixers.append(fr.name)
            elif fr.status == FixerStatus.APPLIED:
                result.applied_fixers.append(fr.name)
            elif fr.status == FixerStatus.FAILED:
                result.failed_fixers.append(fr.name)
            elif fr.status == FixerStatus.UNFIXED:
                result.unfixed_fixers.append(fr.name)

        if not dry_run:
            L0.invalidate_cache()
            post_results = L0._run_all_checks(use_cache=False)
        else:
            post_results = []

        for r in post_results:
            if r.status in (Status.WARN, Status.FAIL):
                result.remaining_issues.append(f"{r.name}: {r.summary}")

        result.requires_human = L0._collect_human_required(
            post_results or L0._run_all_checks()
        )
        return result

    @staticmethod
    def requires_human() -> list[str]:
        """Список причин которые L0 НЕ может починить сам.

        Returns:
            List of human-readable reasons. Empty list = всё либо OK либо
            auto-recoverable через attempt_recovery().
        """
        results = L0._run_all_checks()
        return L0._collect_human_required(results)

    @staticmethod
    def _collect_human_required(results: list[CheckResult]) -> list[str]:
        out: list[str] = []
        for r in results:
            mapping = HUMAN_REQUIRED.get(r.name, {})
            if r.status in mapping:
                if r.name not in ALL_FIXERS:
                    out.append(f"{r.name}: {mapping[r.status]}")
                elif r.name == "fingerprint" and r.status == Status.FAIL:
                    # Fingerprint FAIL = serial mismatch — human должен confirm
                    out.append(f"{r.name}: {mapping[r.status]}")
        return out

    # ── Snapshot ──────────────────────────────────────────────────

    @staticmethod
    def snapshot() -> Snapshot:
        """One-shot typed snapshot всего L0 для UI/log/agent.

        Returns:
            Snapshot dataclass со всеми полями. .to_dict() для JSON serialization.
        """
        results = L0._run_all_checks()
        status_counts: dict[str, int] = {s.value: 0 for s in Status}
        for r in results:
            status_counts[r.status.value] += 1

        identity = L0.identity()
        baseline = L0.baseline_identity()
        baseline_serial = (baseline or {}).get("camera", {}).get("serial")

        return Snapshot(
            layer=L0.LAYER_NAME,
            status=L0._derive_status(results),
            checks_total=len(results),
            status_counts=status_counts,
            identity=identity,
            baseline_serial=baseline_serial,
            requires_human=L0._collect_human_required(results),
        )

    # Backward compat alias (returns dict instead of typed)
    @staticmethod
    def summary() -> dict[str, Any]:
        """Alias для L0.snapshot().to_dict() (backward compat).
        Prefer L0.snapshot() для typed access.
        """
        return L0.snapshot().to_dict()
