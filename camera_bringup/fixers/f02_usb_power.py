"""f02_usb_power — починить USB power management для RealSense D435i.

Что делает:
  1. WriteFile: ставит правильный 99-usb-nosuspend-d435i.rules
     (с persist=0 + ACTION==add|change)
  2. Run: udevadm control --reload
  3. Run: udevadm trigger --action=change --sysname-match=<N-N>

Зачем именно так:
  - control=auto → on: kernel перестаёт пытаться suspend'ить
  - autosuspend=-1: timeout = бесконечный (страховка если control=auto всё же выставится)
  - persist=0: при suspend kernel НЕ сохраняет state — RealSense нужна полная re-init
  - ACTION==add|change: чтобы trigger applied тоже срабатывал, не только add (boot/replug)

Requires root: да (write /etc/udev/rules.d, udevadm).
"""
from __future__ import annotations

from typing import Any

from camera_bringup.fixer import Action, Fixer
from camera_bringup.spec import ACTIVE_INSTANCE, UDEV_RULES_DIR


def _render_power_rule() -> str:
    """Generate USB power rule from active instance hardware spec.

    Note: rule scope = ALL devices matching vendor:product (not per-instance).
    Если будет несколько instances с одним vendor:product (например 2 D435i) —
    rule идентичный, один файл на vendor:product class.
    """
    hw = ACTIVE_INSTANCE.hardware
    return (
        f"# Generated from instance {ACTIVE_INSTANCE.instance_id} —\n"
        f"# scope = all USB devices {hw.usb_vendor_id}:{hw.usb_product_id}.\n"
        f"# Apply on add (boot/replug) and change (manual udevadm trigger).\n"
        f'ACTION=="add|change", SUBSYSTEM=="usb", '
        f'ATTR{{idVendor}}=="{hw.usb_vendor_id}", '
        f'ATTR{{idProduct}}=="{hw.usb_product_id}", \\\n'
        f'  ATTR{{power/control}}="on", \\\n'
        f'  ATTR{{power/autosuspend}}="-1", \\\n'
        f'  ATTR{{power/persist}}="0"\n'
    )


# Имя файла оставлено как было (backward compat с уже установленным на проде).
# Multi-instance с одной vendor:product => идентичный rule, не нужно дублировать.
# Если будет новый camera class — добавим суффикс.
_RULE_PATH = f"{UDEV_RULES_DIR}/99-usb-nosuspend-d435i.rules"


class UsbPowerFixer(Fixer):
    name = "usb_power"
    requires_root = True

    def plan(self, ctx: dict[str, Any]) -> list[Action]:
        # Извлекаем sysfs path который usb_enumerate должен был положить в ctx
        sysfs_path = ctx.get("sysfs_path", "/sys/bus/usb/devices/2-2")
        sysname = sysfs_path.rstrip("/").split("/")[-1]   # "2-2"

        return [
            Action(
                kind="write_file",
                description=f"install USB power rule (from instance {ACTIVE_INSTANCE.instance_id})",
                target=_RULE_PATH,
                payload=_render_power_rule(),
            ),
            Action(
                kind="run",
                description="udevadm control --reload",
                target="udevadm",
                payload="control --reload",
            ),
            Action(
                kind="run",
                description=f"udevadm trigger --action=change --sysname-match={sysname}",
                target="udevadm",
                payload=f"trigger --action=change --sysname-match={sysname}",
            ),
            Action(
                kind="run",
                description="udevadm settle (wait for kernel to apply change)",
                target="udevadm",
                payload="settle --timeout=5",
            ),
        ]
