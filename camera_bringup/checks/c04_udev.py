"""c04_udev — проверить udev rules.

Что проверяет:
  - /etc/udev/rules.d/<instance>.rules существует и совпадает с тем что
    ACTIVE_INSTANCE.render_udev_rule() сейчас бы сгенерировал
  - legacy `.disabled` правила остаются `.disabled` (не active)
  - нет forbidden legacy rules

Правило теперь generated per-instance (не static fixture) — поддерживает
multi-camera setup где у каждой instance свой dev_symlink_name.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from camera_bringup.check import CheckResult, Status, read_file
from camera_bringup.spec import (
    ACTIVE_INSTANCE,
    LEGACY_DISABLED_RULES,
    LEGACY_FORBIDDEN_RULES,
    UDEV_RULE_NAME,
    UDEV_RULES_DIR,
)


def _normalize_rule(text: str) -> str:
    """Нормализуем правило для сравнения: убираем trailing whitespace,
    пустые строки, не considering case в commented sections.

    udev сам игнорирует комментарии — сравниваем только rule lines.
    """
    keep = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        keep.append(stripped)
    return "\n".join(keep)


def check(ctx: dict[str, Any]) -> CheckResult:
    rule_path = f"{UDEV_RULES_DIR}/{UDEV_RULE_NAME}"
    current = read_file(rule_path)
    expected = ACTIVE_INSTANCE.render_udev_rule()

    details: dict[str, Any] = {
        "rule_path": rule_path,
        "rule_present": current is not None,
        "instance_id": ACTIVE_INSTANCE.instance_id,
        "legacy_disabled_checked": list(LEGACY_DISABLED_RULES),
        "legacy_forbidden_checked": list(LEGACY_FORBIDDEN_RULES),
    }

    issues: list[str] = []
    severity = Status.OK

    # 1. main rule existence
    if current is None:
        return CheckResult(
            name="udev",
            status=Status.FAIL,
            summary=f"udev rule {UDEV_RULE_NAME} отсутствует в {UDEV_RULES_DIR}",
            details=details,
            fix_hint=(
                "python3 -m camera_bringup apply --only udev "
                "(сгенерирует rule из InstanceSpec, reload + trigger)"
            ),
        )

    # 2. content matches generated rule (semantic, не byte-level)
    if _normalize_rule(current) != _normalize_rule(expected):
        diff = list(
            difflib.unified_diff(
                _normalize_rule(expected).splitlines(),
                _normalize_rule(current).splitlines(),
                fromfile=f"expected (instance={ACTIVE_INSTANCE.instance_id})",
                tofile="current",
                lineterm="",
            )
        )
        issues.append("текущее правило расходится с сгенерированным от InstanceSpec")
        details["diff"] = "\n".join(diff[:40])
        severity = Status.WARN

    # 3. legacy disabled rules — должны остаться .disabled
    legacy_active: list[str] = []
    for legacy in LEGACY_DISABLED_RULES:
        # Если без `.disabled` расширения — значит кто-то его активировал
        without_suffix = legacy.replace(".disabled", "")
        if Path(f"{UDEV_RULES_DIR}/{without_suffix}").is_file():
            legacy_active.append(without_suffix)

    if legacy_active:
        issues.append(f"legacy rules активированы: {', '.join(legacy_active)}")
        severity = Status.FAIL

    # 4. forbidden rules — не должно быть вовсе
    forbidden_present: list[str] = []
    for forbidden in LEGACY_FORBIDDEN_RULES:
        if Path(f"{UDEV_RULES_DIR}/{forbidden}").is_file():
            forbidden_present.append(forbidden)

    if forbidden_present:
        issues.append(f"forbidden legacy rules: {', '.join(forbidden_present)}")
        severity = Status.FAIL

    details["legacy_active"] = legacy_active
    details["forbidden_present"] = forbidden_present

    if not issues:
        return CheckResult(
            name="udev",
            status=Status.OK,
            summary=f"{UDEV_RULE_NAME} установлено, совпадает с fixture, legacy не активны",
            details=details,
        )

    return CheckResult(
        name="udev",
        status=severity,
        summary="; ".join(issues),
        details=details,
        fix_hint=(
            f"привести {rule_path} к fixture, удалить активные legacy "
            "(или переименовать в .disabled); udevadm control --reload + trigger"
        ),
    )
