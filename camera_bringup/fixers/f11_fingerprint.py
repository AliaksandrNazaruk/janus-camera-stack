"""f11_fingerprint — записать/обновить baseline `/var/lib/camera/fingerprint.json`.

Семантика:
  - Если файла нет → создать с current state, first_seen_utc=now, verify_count=1
  - Если файл есть → preserve first_seen_utc, increment verify_count, обновить
    last_verified_utc, обновить все остальные поля camera/host из current

Что НЕ делает:
  - Не сравнивает с baseline (это работа check'а)
  - Не запрашивает confirm для FAIL-diffs (если serial mismatch и пользователь
    apply'ит fingerprint — он сознательно «принимает» новую камеру как baseline)

Requires root: ДА (write в /var/lib/camera/).
"""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

from camera_bringup.fixer import Action, Fixer
from camera_bringup.realsense_query import primary_device
from camera_bringup.signing import (
    attach_signature,
    generate_secret,
    load_secret,
    secret_exists,
)
from camera_bringup.spec import (
    FINGERPRINT_DIR,
    FINGERPRINT_PATH,
    FINGERPRINT_SCHEMA_VERSION,
    HMAC_SECRET_DIR,
    HMAC_SECRET_PATH,
    USB_PRODUCT_ID,
    USB_VENDOR_ID,
)


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _build_fingerprint(ctx: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Собрать новый fingerprint dict, preserving history если baseline есть."""
    device = primary_device() or {}
    sysfs_path = ctx.get("sysfs_path")
    v4l_dev = ctx.get("v4l_dev")
    now = _now_utc()

    history = {}
    if baseline:
        history = baseline.get("history", {})
    fp = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "camera": {
            "vendor_id": USB_VENDOR_ID,
            "product_id": USB_PRODUCT_ID,
            "serial": device.get("serial"),
            "firmware": device.get("firmware"),
            "usb_type": device.get("usb_type"),
            "product_name": device.get("name"),
            "product_line": device.get("product_line"),
        },
        "host": {
            "sysfs_path": sysfs_path,
            "v4l_dev": v4l_dev,
            "hostname": socket.gethostname(),
        },
        # Factory calibration из EEPROM камеры (intrinsics per sensor).
        # Не должна меняться кроме как при FW update / hardware service.
        # CV pipeline (3D reconstruction, undistortion) использует эти числа.
        "calibration": device.get("calibration", {}),
        "history": {
            "first_seen_utc": history.get("first_seen_utc", now),
            "last_verified_utc": now,
            "verify_count": int(history.get("verify_count", 0)) + 1,
        },
    }
    return fp


class FingerprintFixer(Fixer):
    name = "fingerprint"
    requires_root = True

    def plan(self, ctx: dict[str, Any]) -> list[Action]:
        # Если файла нет — добавить ensure dir + write
        path = Path(FINGERPRINT_PATH)
        baseline = None
        if path.is_file():
            try:
                baseline = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                baseline = None

        actions: list[Action] = []

        # Ensure parent dir для fingerprint (mkdir idempotent)
        if not Path(FINGERPRINT_DIR).is_dir():
            actions.append(Action(
                kind="run",
                description=f"mkdir -p {FINGERPRINT_DIR}",
                target="mkdir",
                payload=f"-p {FINGERPRINT_DIR}",
            ))

        # Ensure HMAC secret dir + generate secret if missing.
        # Secret = 32 random bytes, mode 600 root:root.
        if not secret_exists():
            actions.append(Action(
                kind="run",
                description=f"mkdir -p {HMAC_SECRET_DIR}",
                target="mkdir",
                payload=f"-p {HMAC_SECRET_DIR}",
            ))
            # write_file action — writes inplace; mode не контролируется этим action.
            # Для root-only: после write делаем chmod 600 через separate run action.
            actions.append(Action(
                kind="write_file",
                description=f"generate HMAC secret {HMAC_SECRET_PATH} (32 random bytes)",
                target=HMAC_SECRET_PATH,
                payload=generate_secret().hex(),  # сохраняем hex для удобства
            ))
            # chmod 600 для secret (только root читает)
            actions.append(Action(
                kind="run",
                description=f"chmod 600 {HMAC_SECRET_PATH}",
                target="chmod",
                payload=f"600 {HMAC_SECRET_PATH}",
            ))

        # Build new fingerprint payload + подписать (если secret уже есть, или
        # будет создан выше; в last case подпись в actual execute, но action plan
        # generation НЕ может execute action — поэтому signature будет добавлена
        # на 2nd apply pass. Для cleanness — рекомендуем `apply --only fingerprint`
        # вызывать дважды на cold install: 1й создаёт secret, 2й подписывает.)
        fp = _build_fingerprint(ctx, baseline)
        secret = load_secret()
        if secret:
            try:
                # Если строка hex — decode для use as bytes
                secret_bytes = bytes.fromhex(secret.decode())
            except (ValueError, UnicodeDecodeError):
                secret_bytes = secret
            fp = attach_signature(fp, secret_bytes)
        content = json.dumps(fp, indent=2, sort_keys=True) + "\n"

        signed = "signed" if "_hmac" in fp else "unsigned"
        actions.append(Action(
            kind="write_file",
            description=(
                f"write {FINGERPRINT_PATH} "
                f"(serial={fp['camera']['serial']}, "
                f"verify_count={fp['history']['verify_count']}, "
                f"{signed})"
            ),
            target=FINGERPRINT_PATH,
            payload=content,
        ))
        return actions
