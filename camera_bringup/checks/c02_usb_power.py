"""c02_usb_power — проверить USB power management для RealSense.

Что проверяет:
  - power/control должен быть "on" (kernel не должен пытаться suspend'ить)
  - power/autosuspend = -1 (timeout disabled)
  - power/persist = 0 (не сохранять state при suspend — для RealSense
    нужна re-init)
  - power/runtime_status = active

Что блокирует:
  - autosuspend > 0 при control=auto = камера засыпает и не просыпается
    (известный bug D435i). Это FAIL.
  - control=auto при autosuspend=-1 — формально работает (timeout
    бесконечный = не суспендит), но рискованно (любое изменение timeout
    включит suspend). WARN.

Зависит от ctx['sysfs_path'] из c01_usb_enumerate.
"""
from __future__ import annotations

from typing import Any

from camera_bringup.check import CheckResult, Status
from camera_bringup.ports import SystemPort, default_system
from camera_bringup.spec import USB_POWER_SPEC


def _read_int(system: SystemPort, path: str) -> int | None:
    raw = system.read_file(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def check(ctx: dict[str, Any], system: SystemPort | None = None) -> CheckResult:
    """Reference migration to SystemPort (ports/adapters pattern).
    Production использует RealSystemPort; tests подменяют FakeSystemPort
    через kwarg. См. ADR 0006.
    """
    system = system or default_system()

    sysfs_path = ctx.get("sysfs_path")
    if not sysfs_path:
        return CheckResult(
            name="usb_power",
            status=Status.SKIP,
            summary="нет sysfs_path в ctx (usb_enumerate не нашёл камеру)",
        )

    power = f"{sysfs_path}/power"
    control = (system.read_file(f"{power}/control") or "").strip()
    autosuspend = _read_int(system, f"{power}/autosuspend")
    persist = _read_int(system, f"{power}/persist")
    runtime_status = (system.read_file(f"{power}/runtime_status") or "").strip()

    details = {
        "control": control,
        "autosuspend": autosuspend,
        "persist": persist,
        "runtime_status": runtime_status,
        "expected": {
            "control": USB_POWER_SPEC.control,
            "autosuspend": USB_POWER_SPEC.autosuspend,
            "persist": USB_POWER_SPEC.persist,
            "runtime_status": USB_POWER_SPEC.runtime_status,
        },
    }

    deviations = []
    severity = Status.OK

    # Критичное: control=auto + autosuspend > 0 = камера будет суспендиться
    if control == "auto" and (autosuspend is not None and autosuspend > 0):
        return CheckResult(
            name="usb_power",
            status=Status.FAIL,
            summary=(
                f"control=auto + autosuspend={autosuspend}s — камера будет суспендиться "
                "и может не проснуться (FW bug D435i)"
            ),
            details=details,
            fix_hint=(
                f"echo on > {power}/control; "
                f"echo -1 > {power}/autosuspend "
                "(применить udev rules постоянно)"
            ),
        )

    if control != USB_POWER_SPEC.control:
        deviations.append(f"control={control!r} (expected {USB_POWER_SPEC.control!r})")
        # control=auto + autosuspend=-1 формально работает, но рискованно
        severity = Status.WARN

    if autosuspend != USB_POWER_SPEC.autosuspend:
        deviations.append(f"autosuspend={autosuspend} (expected {USB_POWER_SPEC.autosuspend})")
        severity = Status.WARN if severity != Status.FAIL else severity

    if persist != USB_POWER_SPEC.persist:
        deviations.append(f"persist={persist} (expected {USB_POWER_SPEC.persist})")
        # persist=1 — kernel может вернуть камеру в broken state. WARN.
        severity = Status.WARN if severity != Status.FAIL else severity

    if runtime_status != USB_POWER_SPEC.runtime_status:
        deviations.append(
            f"runtime_status={runtime_status!r} (expected {USB_POWER_SPEC.runtime_status!r})"
        )
        # Камера не active = большая проблема
        severity = Status.FAIL

    if not deviations:
        return CheckResult(
            name="usb_power",
            status=Status.OK,
            summary=f"control={control} autosuspend={autosuspend} persist={persist} {runtime_status}",
            details=details,
        )

    return CheckResult(
        name="usb_power",
        status=severity,
        summary="; ".join(deviations),
        details=details,
        fix_hint=(
            "проверить /etc/udev/rules.d/99-realsense-power.rules + "
            "99-usb-nosuspend-d435i.rules; udevadm trigger --action=change"
        ),
    )
