"""f04_udev — установить udev rule сгенерированный из активного InstanceSpec.

Что делает:
  1. WriteFile: render_udev_rule() → /etc/udev/rules.d/<instance>.rules
  2. Run: udevadm control --reload
  3. Run: udevadm trigger --subsystem-match=video4linux
  4. Run: udevadm settle

Идемпотентен — write_file no-op если content identical.

Requires root: да (write /etc/udev/rules.d, udevadm).
"""
from __future__ import annotations

from typing import Any

from camera_bringup.fixer import Action, Fixer
from camera_bringup.spec import ACTIVE_INSTANCE, UDEV_RULE_NAME, UDEV_RULES_DIR


class UdevFixer(Fixer):
    name = "udev"
    requires_root = True

    def plan(self, ctx: dict[str, Any]) -> list[Action]:
        rule_path = f"{UDEV_RULES_DIR}/{UDEV_RULE_NAME}"
        rule_content = ACTIVE_INSTANCE.render_udev_rule()

        return [
            Action(
                kind="write_file",
                description=f"install {UDEV_RULE_NAME} (from instance {ACTIVE_INSTANCE.instance_id})",
                target=rule_path,
                payload=rule_content,
            ),
            Action(
                kind="run",
                description="udevadm control --reload",
                target="udevadm",
                payload="control --reload",
            ),
            Action(
                kind="run",
                description="udevadm trigger --subsystem-match=video4linux",
                target="udevadm",
                payload="trigger --subsystem-match=video4linux",
            ),
            Action(
                kind="run",
                description="udevadm settle",
                target="udevadm",
                payload="settle --timeout=5",
            ),
        ]
