"""Use-cases for the encoder admin routes.

Orchestration — validation, audit, response shaping — over the encoder_admin /
encoder_env / systemd adapters. Extracted from admin_dashboard (C-04); behavior and
audit strings preserved verbatim.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from app.services import encoder_admin, encoder_env, systemd
from app.services.audit_log import audit


# ── domain errors (the route maps these to HTTP; this layer stays FastAPI-free) ──
class UnknownEncoderFamily(Exception):
    """Unknown encoder family. Route maps to 400 (message carried verbatim)."""


class BadEncoderInstance(Exception):
    """An instanced family was given a missing/invalid instance name. Route maps to 400."""


class EncoderExecFailed(Exception):
    """The encoder-admin CLI invocation failed. Route maps to 500 (message carried verbatim)."""


class EncoderActionResponse(BaseModel):
    family: str
    instance: Optional[str] = None
    action: str
    ok: bool
    rc: int
    stderr: Optional[str] = None


class EncoderInstanceStatus(BaseModel):
    family: str
    instance: Optional[str] = None
    unit: str
    active: bool
    active_enter_timestamp: Optional[str] = None
    ffmpeg_pid: Optional[int] = None
    rtp_port: Optional[int] = None
    tuning_env_path: Optional[str] = None
    contract_env_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    bitrate_kbps: Optional[int] = None


def validate_encoder_target(family: str, instance: Optional[str]) -> None:
    if family not in encoder_admin.ENCODER_FAMILIES:
        raise UnknownEncoderFamily(
            f"Unknown family {family!r}; allowed: {sorted(encoder_admin.ENCODER_FAMILIES)}")
    if family in encoder_admin.INSTANCED_FAMILIES:
        if not instance or not encoder_admin.INSTANCE_RE.match(instance):
            raise BadEncoderInstance(
                f"Family {family!r} requires alphanumeric instance name (max 32 chars)")


def encoder_action(action: str, family: str, instance: Optional[str]) -> EncoderActionResponse:
    """Invoke encoder-admin, audit, shape response (was admin_dashboard._encoder_admin)."""
    try:
        rc, stderr = encoder_admin.invoke(action, family, instance)
    except RuntimeError as exc:
        audit("admin_dashboard.encoder.exec_failed",
              {"family": family, "instance": instance, "action": action, "error": str(exc)[:120]})
        raise EncoderExecFailed(f"encoder-admin exec failed: {exc}") from exc
    audit(f"admin_dashboard.encoder.{action}" + ("" if rc == 0 else "_failed"),
          {"family": family, "instance": instance, "rc": rc, "stderr": stderr[:200]})
    return EncoderActionResponse(family=family, instance=instance, action=action,
                                 ok=(rc == 0), rc=rc, stderr=stderr if stderr else None)


def start_encoder(family: str, instance: Optional[str]) -> EncoderActionResponse:
    validate_encoder_target(family, instance)
    return encoder_action("start", family, instance)


def stop_encoder(family: str, instance: Optional[str]) -> EncoderActionResponse:
    validate_encoder_target(family, instance)
    return encoder_action("stop", family, instance)


def instance_status(family: str, instance: Optional[str]) -> EncoderInstanceStatus:
    """Combine systemctl + env files for one encoder instance."""
    unit_name = f"{family}@{instance}" if instance else family
    info = systemd.show(unit_name + ".service") or {}
    active = info.get("ActiveState") == "active"
    main_pid_raw = info.get("MainPID", "0")
    ffmpeg_pid = int(main_pid_raw) if main_pid_raw.isdigit() and int(main_pid_raw) > 0 else None
    ts = info.get("ActiveEnterTimestamp") or None

    rtp_port = tuning_path = contract_path = None
    width = height = fps = bitrate = None
    if instance:
        tuning_path = encoder_env.ENV_DIR / f"{family}-{instance}.tuning.env"
        contract_path = encoder_env.ENV_DIR / f"{family}-{instance}.contract.env"
        contract_env = encoder_env.read_env_file(contract_path)
        tuning_env = encoder_env.read_env_file(tuning_path)
        try:
            rtp_port = int(contract_env.get("PORT", "")) if contract_env.get("PORT") else None
        except ValueError:
            rtp_port = None
        try:
            width = int(tuning_env.get("WIDTH", "")) if tuning_env.get("WIDTH") else None
            height = int(tuning_env.get("HEIGHT", "")) if tuning_env.get("HEIGHT") else None
            fps = int(tuning_env.get("FPS", "")) if tuning_env.get("FPS") else None
            bitrate = int(tuning_env.get("BITRATE_KBPS", "")) if tuning_env.get("BITRATE_KBPS") else None
        except ValueError:
            pass

    return EncoderInstanceStatus(
        family=family, instance=instance, unit=unit_name,
        active=active, active_enter_timestamp=ts, ffmpeg_pid=ffmpeg_pid, rtp_port=rtp_port,
        tuning_env_path=str(tuning_path) if tuning_path else None,
        contract_env_path=str(contract_path) if contract_path else None,
        width=width, height=height, fps=fps, bitrate_kbps=bitrate,
    )


def list_instances() -> List[EncoderInstanceStatus]:
    return [instance_status(f, i) for f, i in encoder_admin.discover_units()]
