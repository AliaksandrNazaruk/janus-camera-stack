"""Contract / tuning env store.

Atomic writes of ``/etc/robot/rs-<sensor>.{contract,tuning}.env`` that rs-stream.sh reads — the
allocated PORT + RTP target host (the L4-managed contract) and the operator-editable tuning defaults.
Extracted verbatim from sensor_lifecycle.py (Phase 4 / A-04).
"""
from __future__ import annotations

from pathlib import Path


def _contract_path(sensor: str) -> Path:
    return Path(f"/etc/robot/rs-{sensor}.contract.env")


def _tuning_path(sensor: str) -> Path:
    return Path(f"/etc/robot/rs-{sensor}.tuning.env")


def _write_contract_env(sensor: str, rtp_port: int,
                        rtp_target_host: str = "127.0.0.1") -> None:
    """Update rs-<sensor>.contract.env with allocated PORT + RTP target host.
    Atomic write.

    RTP_TARGET_HOST (G4 contract) is where rs-stream.sh sends RTP. Defaults to
    loopback — for the LOCAL gateway camera this preserves prior behaviour
    exactly. A remote producer node's contract carries the gateway LAN IP so
    its encoder targets the gateway instead of its own loopback.
    """
    p = _contract_path(sensor)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        "# === CONTRACT — managed by L4 sensor_lifecycle (X3 dynamic allocation) ===\n"
        f"PORT=\"{rtp_port}\"\n"
        f"RTP_TARGET_HOST=\"{rtp_target_host}\"\n"
    )
    tmp.replace(p)
    p.chmod(0o644)


def _ensure_default_tuning_env(sensor: str) -> None:
    """Write default rs-<sensor>.tuning.env if missing. Idempotent — preserves
    operator changes on re-Initialize. Defaults match rs-stream.sh fallbacks.
    """
    p = _tuning_path(sensor)
    if p.exists():
        return
    defaults = {
        "WIDTH": 640, "HEIGHT": 480, "FPS": 15,
        "BITRATE_KBPS": {"depth": 1000, "color": 900}.get(sensor, 800),
        "GOP": 15, "PRESET": "veryfast", "TUNE": "zerolatency",
        "ROTATION": 0,
    }
    if sensor == "color":
        # Color parity with retired rtp-rgb: emit periodic MJPEG still for
        # /api/v1/color_camera/snapshot.jpg (rs-stream.sh splits the output).
        defaults["SNAPSHOT_PATH"] = "/run/realsense/color-snapshot.jpg"
        defaults["SNAPSHOT_FPS"] = 1
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        f"# === TUNING — operator-editable. Initial defaults written by L4 on first Initialize ===\n"
        f"# Edit + `sudo systemctl restart rs-stream@{sensor}.service` to apply.\n"
        + "\n".join(f'{k}="{v}"' for k, v in defaults.items()) + "\n"
    )
    tmp.replace(p)
    p.chmod(0o644)
