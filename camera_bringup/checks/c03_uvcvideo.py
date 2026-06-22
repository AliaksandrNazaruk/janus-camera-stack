"""c03_uvcvideo — проверить uvcvideo module и его параметры.

Что проверяет:
  - uvcvideo загружен (lsmod)
  - /sys/module/uvcvideo/parameters/* совпадает со спекой
  - /etc/modprobe.d/uvcvideo.conf содержит те же значения (consistency
    между «что сейчас» и «что будет после reboot»)

Что блокирует:
  - модуль не загружен — FAIL
  - quirks неверны — WARN (часто работает, но может быть FW bug)
  - timeout слишком велик (default 5000ms) — WARN, медленный fail detection
  - rumtime params != modprobe.conf params — WARN (после reboot вернётся к conf)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from camera_bringup.check import CheckResult, Status, read_file, read_int
from camera_bringup.spec import MODPROBE_CONF, UVCVIDEO_SPEC


def _parse_modprobe_options(path: str) -> dict[str, str]:
    """Распарсить `options uvcvideo nodrop=1 timeout=500 ...` строки."""
    raw = read_file(path)
    if raw is None:
        return {}
    opts: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # `options uvcvideo nodrop=1 timeout=500 quirks=128`
        m = re.match(r"options\s+uvcvideo\s+(.+)", line)
        if not m:
            continue
        for tok in m.group(1).split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                opts[k] = v
    return opts


def check(ctx: dict[str, Any]) -> CheckResult:
    # Загружен ли модуль
    sys_module = Path("/sys/module/uvcvideo")
    if not sys_module.is_dir():
        return CheckResult(
            name="uvcvideo",
            status=Status.FAIL,
            summary="модуль uvcvideo НЕ загружен",
            fix_hint="modprobe uvcvideo (после починки modprobe.d/uvcvideo.conf)",
        )

    # Runtime параметры
    runtime_nodrop = read_int(f"{sys_module}/parameters/nodrop")
    runtime_timeout = read_int(f"{sys_module}/parameters/timeout")
    runtime_quirks = read_int(f"{sys_module}/parameters/quirks")

    # Что в modprobe.d (после reboot/reload)
    conf_opts = _parse_modprobe_options(MODPROBE_CONF)
    conf_nodrop = int(conf_opts.get("nodrop", -1)) if "nodrop" in conf_opts else None
    conf_timeout = int(conf_opts.get("timeout", -1)) if "timeout" in conf_opts else None
    conf_quirks = int(conf_opts.get("quirks", -1)) if "quirks" in conf_opts else None

    details = {
        "runtime": {
            "nodrop": runtime_nodrop,
            "timeout": runtime_timeout,
            "quirks": runtime_quirks,
        },
        "modprobe_conf": {
            "path": MODPROBE_CONF,
            "nodrop": conf_nodrop,
            "timeout": conf_timeout,
            "quirks": conf_quirks,
        },
        "expected": {
            "nodrop": UVCVIDEO_SPEC.nodrop,
            "timeout": UVCVIDEO_SPEC.timeout,
            "quirks": UVCVIDEO_SPEC.quirks,
        },
    }

    issues = []
    severity = Status.OK

    def _cmp(name: str, runtime, conf, expected):
        nonlocal severity
        if runtime != expected:
            issues.append(f"runtime.{name}={runtime} (expected {expected})")
            severity = Status.WARN
        if conf is not None and conf != expected:
            issues.append(f"conf.{name}={conf} (expected {expected})")
            severity = Status.WARN
        if runtime is not None and conf is not None and runtime != conf:
            issues.append(f"runtime.{name}={runtime} != conf.{name}={conf} (drift after reload)")
            severity = Status.WARN

    _cmp("nodrop", runtime_nodrop, conf_nodrop, UVCVIDEO_SPEC.nodrop)
    _cmp("timeout", runtime_timeout, conf_timeout, UVCVIDEO_SPEC.timeout)
    _cmp("quirks", runtime_quirks, conf_quirks, UVCVIDEO_SPEC.quirks)

    if not issues:
        return CheckResult(
            name="uvcvideo",
            status=Status.OK,
            summary=(
                f"loaded; nodrop={runtime_nodrop} timeout={runtime_timeout} "
                f"quirks={runtime_quirks} (matches modprobe.conf)"
            ),
            details=details,
        )

    return CheckResult(
        name="uvcvideo",
        status=severity,
        summary="; ".join(issues),
        details=details,
        fix_hint=(
            f"привести {MODPROBE_CONF} к спеке + rmmod uvcvideo && modprobe uvcvideo "
            "(или reboot если камера используется)"
        ),
    )
