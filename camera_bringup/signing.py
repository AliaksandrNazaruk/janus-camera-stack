"""HMAC-SHA256 signing для fingerprint.json — tamper detection.

Threat model:
  - Attacker with root может подделать /var/lib/camera/<id>.json
  - HMAC секрет в /etc/camera_bringup/secret.key (mode 600 root:root) делает
    подделку detectable (без знания секрета — invalid signature)

Modes:
  - **Signed mode**: secret file существует → fingerprint содержит "_hmac" field
  - **Legacy mode**: secret отсутствует → fingerprint без signature, не
    проверяется (backward compat с старыми baseline)

Migration: первое apply создаёт secret + подписывает existing baseline.
Subsequent verify проверяет signature.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any

from camera_bringup.spec import HMAC_SECRET_PATH


def secret_exists() -> bool:
    return Path(HMAC_SECRET_PATH).is_file()


def load_secret() -> bytes | None:
    """Read HMAC secret. None если файл отсутствует или нет прав."""
    try:
        with open(HMAC_SECRET_PATH, "rb") as f:
            data = f.read().strip()
        return data if data else None
    except (OSError, PermissionError):
        return None


def generate_secret() -> bytes:
    """Сгенерить новый 32-byte secret через os.urandom."""
    return secrets.token_bytes(32)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Канонизируем JSON для подписи: sort_keys + no whitespace."""
    # Исключаем _hmac field из payload-под-подпись (chicken-egg)
    cleaned = {k: v for k, v in payload.items() if k != "_hmac"}
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(payload: dict[str, Any], secret: bytes) -> str:
    """Возвращает hex HMAC-SHA256."""
    return hmac.new(secret, _canonical_json(payload), hashlib.sha256).hexdigest()


def verify(payload: dict[str, Any], secret: bytes) -> bool:
    """True если _hmac в payload корректный."""
    expected = payload.get("_hmac")
    if not expected:
        return False
    actual = sign(payload, secret)
    return hmac.compare_digest(expected, actual)


def attach_signature(payload: dict[str, Any], secret: bytes) -> dict[str, Any]:
    """Добавить _hmac field, return new dict."""
    out = dict(payload)
    out["_hmac"] = sign(out, secret)
    return out
