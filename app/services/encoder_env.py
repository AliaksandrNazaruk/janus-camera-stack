"""Encoder env-file IO adapter.

The only place the per-encoder `/etc/robot/<family>-<instance>.{tuning,contract}.env`
files are read or written. Extracted from admin_dashboard (C-04); behavior verbatim.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.encoder_admin import INSTANCE_RE


class InvalidEncoderInstanceName(Exception):
    """Encoder instance name failed the INSTANCE_RE whitelist. Route maps to 400 (message verbatim)."""


ENV_DIR = Path("/etc/robot")


class EncoderEnvSpec(BaseModel):
    """Subset of env vars that operator sets via UI. Adapter scripts read these
    from /etc/robot/<family>-<instance>.tuning.env at startup."""
    DEVICE: Optional[str] = Field(None, max_length=128, description="/dev/video* for V4L2, rtsp://... for RTSP")
    PIX_FMT: Optional[str] = Field("", max_length=32)
    WIDTH: int = Field(640, ge=160, le=4096)
    HEIGHT: int = Field(480, ge=120, le=4096)
    FPS: int = Field(30, ge=1, le=120)
    BITRATE_KBPS: int = Field(1500, ge=200, le=20000)
    GOP: int = Field(30, ge=1, le=300)
    PRESET: str = Field("veryfast", max_length=16)
    TUNE: str = Field("zerolatency", max_length=16)
    ROTATION: int = Field(0, ge=0, le=270)


def read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def write_env_files(family: str, instance: str, env: EncoderEnvSpec, rtp_port: int) -> List[str]:
    """Write tuning.env + contract.env atomically. Returns paths written."""
    if not INSTANCE_RE.match(instance):
        raise InvalidEncoderInstanceName(f"Invalid instance name {instance!r}")
    ENV_DIR.mkdir(exist_ok=True)

    tuning = ENV_DIR / f"{family}-{instance}.tuning.env"
    contract = ENV_DIR / f"{family}-{instance}.contract.env"

    tuning_text = f"""# Auto-written by admin_dashboard at {family}-{instance}
DEVICE="{env.DEVICE or ''}"
PIX_FMT="{env.PIX_FMT or ''}"
WIDTH="{env.WIDTH}"
HEIGHT="{env.HEIGHT}"
FPS="{env.FPS}"
BITRATE_KBPS="{env.BITRATE_KBPS}"
GOP="{env.GOP}"
PRESET="{env.PRESET}"
TUNE="{env.TUNE}"
ROTATION="{env.ROTATION}"
SNAPSHOT_PATH=""
SNAPSHOT_FPS="1"
"""
    contract_text = f"""# Auto-written by admin_dashboard
PORT="{rtp_port}"
"""

    for path, text in [(tuning, tuning_text), (contract, contract_text)]:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.rename(tmp, path)
    return [str(tuning), str(contract)]
