"""InstanceSpec — declarative описание одной камеры-инстанса.

L0 = template (этот пакет). Каждая физическая камера = instance этого template'а
с собственным InstanceSpec (загружается из instances/<id>.toml).

Multi-camera deployment:
  - Каждая камера → отдельный TOML файл в instances/
  - Каждая instance имеет unique dev_symlink_name (например cam-rgb, cam-rear)
  - CLI/ENV выбирает active instance: `CAMERA_BRINGUP_INSTANCE=cam-rear ...`
  - Per-instance state: fingerprint, lock, udev rule

Discriminator между D435i (USB iSerial у них = 0):
  - usb_port_hint (e.g. "2-2") — стабильно если камера в фиксированном порту
  - expected_serial — для post-baseline validation (через pyrealsense2)

Single-camera deployment (default): instance "cam-rgb" loaded автоматически.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib


@dataclass(frozen=True)
class StreamProfile:
    pixel_format: str = "YUYV"
    width: int = 640
    height: int = 480
    fps: int = 15


@dataclass(frozen=True)
class HardwareSpec:
    usb_vendor_id: str = "8086"
    usb_product_id: str = "0b3a"
    usb_interface_num: str = "03"           # которая capture-нода нам нужна
    expected_serial: str | None = None   # post-baseline (через pyrealsense2)
    usb_port_hint: str | None = None     # e.g. "2-2" для port-based udev


@dataclass(frozen=True)
class FirmwareSpec:
    min_bcd: int = 5100


@dataclass(frozen=True)
class InstanceSpec:
    """Полное описание одной камеры-инстанса."""
    instance_id: str
    friendly_name: str = ""
    location: str = ""

    hardware: HardwareSpec = field(default_factory=HardwareSpec)
    stream: StreamProfile = field(default_factory=StreamProfile)
    firmware: FirmwareSpec = field(default_factory=FirmwareSpec)

    # /dev/<symlink_name> — alias который создаст udev rule
    dev_symlink_name: str = "cam-rgb"

    # ── Derived paths (computed from instance_id) ─────────────────────

    @property
    def udev_rule_filename(self) -> str:
        """E.g. '99-cam-rgb.rules' — уникальный per-instance."""
        return f"99-{self.dev_symlink_name}.rules"

    # ── Rendering ─────────────────────────────────────────────────────

    def render_udev_rule(self) -> str:
        """Generate udev rule text from this spec.

        Creates the /dev/<symlink> node + the systemd .device unit (via
        SYSTEMD_ALIAS + TAG=systemd). Phase 2: encoder autostart (SYSTEMD_WANTS=
        rtp-rgb@…) was REMOVED — stream lifecycle is reconciler/dashboard-driven
        (sensor-reconcile.service), не udev-device-binding. The mux owns the
        camera через librealsense, not /dev/<symlink> directly.
        """
        port_match = ""
        if self.hardware.usb_port_hint:
            port_match = f'KERNELS=="{self.hardware.usb_port_hint}", '

        return (
            f"# Generated from instance {self.instance_id} — DO NOT EDIT MANUALLY.\n"
            f"# See camera_bringup/instances/{self.instance_id}.toml + InstanceSpec.render_udev_rule().\n"
            f"# Auto-installed by camera_bringup apply --only udev.\n"
            f'SUBSYSTEM=="video4linux", KERNEL=="video*", \\\n'
            f'  ENV{{ID_VENDOR_ID}}=="{self.hardware.usb_vendor_id}", '
            f'ENV{{ID_MODEL_ID}}=="{self.hardware.usb_product_id}", '
            f'ENV{{ID_USB_INTERFACE_NUM}}=="{self.hardware.usb_interface_num}", \\\n'
            f'  ENV{{ID_V4L_CAPABILITIES}}==":capture:", \\\n'
            f'  {port_match}\\\n'
            f'  SYMLINK+="{self.dev_symlink_name}", \\\n'
            f'  ENV{{SYSTEMD_ALIAS}}="/dev/{self.dev_symlink_name}", \\\n'
            f'  TAG+="systemd"\n'
        )


# ── Loader ────────────────────────────────────────────────────────────

DEFAULT_INSTANCE_ID = "cam-rgb"


def _instances_dir() -> Path:
    """Каталог с TOML файлами инстансов."""
    explicit = os.environ.get("CAMERA_BRINGUP_INSTANCES_DIR")
    if explicit:
        return Path(explicit)
    # default — рядом с пакетом
    return Path(__file__).resolve().parent / "instances"


def load_instance(instance_id: str | None = None) -> InstanceSpec:
    """Загрузить InstanceSpec из TOML.

    Если instance_id не указан:
      1. Берём из CAMERA_BRINGUP_INSTANCE env
      2. Default: 'cam-rgb'
    """
    iid = instance_id or os.environ.get("CAMERA_BRINGUP_INSTANCE", DEFAULT_INSTANCE_ID)
    path = _instances_dir() / f"{iid}.toml"

    if not path.is_file():
        raise FileNotFoundError(
            f"Instance config not found: {path}. "
            f"Available: {[p.stem for p in _instances_dir().glob('*.toml')]}"
        )

    with open(path, "rb") as f:
        data = tomllib.load(f)

    hw = data.get("hardware", {})
    stream = data.get("stream", {})
    fw = data.get("firmware", {})
    dev = data.get("dev", {})

    return InstanceSpec(
        instance_id=data.get("instance_id", iid),
        friendly_name=data.get("friendly_name", ""),
        location=data.get("location", ""),
        hardware=HardwareSpec(
            usb_vendor_id=hw.get("usb_vendor_id", "8086"),
            usb_product_id=hw.get("usb_product_id", "0b3a"),
            usb_interface_num=hw.get("usb_interface_num", "03"),
            expected_serial=hw.get("expected_serial"),
            usb_port_hint=hw.get("usb_port_hint"),
        ),
        stream=StreamProfile(
            pixel_format=stream.get("pixel_format", "YUYV"),
            width=stream.get("width", 640),
            height=stream.get("height", 480),
            fps=stream.get("fps", 15),
        ),
        firmware=FirmwareSpec(
            min_bcd=fw.get("min_bcd", 5100),
        ),
        dev_symlink_name=dev.get("symlink_name", "cam-rgb"),
    )


def list_instances() -> list[str]:
    """Список всех instance_id (по TOML файлам в instances/)."""
    return sorted(p.stem for p in _instances_dir().glob("*.toml"))
