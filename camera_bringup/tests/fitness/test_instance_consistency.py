"""Fitness: каждый instance TOML должен быть валиден и self-consistent."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.fitness


class TestInstanceFiles:
    def test_default_instance_loads(self):
        from camera_bringup.instance import load_instance
        spec = load_instance("cam-rgb")
        assert spec.instance_id == "cam-rgb"
        assert spec.hardware.usb_vendor_id == "8086"

    def test_all_instance_files_parseable(self):
        """Каждый .toml в instances/ должен загружаться без ошибок."""
        from camera_bringup.instance import list_instances, load_instance
        ids = list_instances()
        assert ids, "must have at least one instance file"
        for iid in ids:
            spec = load_instance(iid)
            assert spec.instance_id == iid, (
                f"instance_id mismatch in {iid}.toml: {spec.instance_id}"
            )
            assert spec.dev_symlink_name, f"{iid}: dev_symlink_name пустой"
            assert spec.hardware.usb_vendor_id, f"{iid}: usb_vendor_id пустой"
            assert spec.hardware.usb_product_id, f"{iid}: usb_product_id пустой"


class TestUdevRuleGeneration:
    def test_render_includes_required_match_tokens(self):
        """Generated udev rule должен иметь все обязательные match tokens."""
        from camera_bringup.instance import load_instance
        spec = load_instance("cam-rgb")
        rule = spec.render_udev_rule()
        # Critical tokens — без них udev rule не сработает
        assert 'SUBSYSTEM=="video4linux"' in rule
        assert 'KERNEL=="video*"' in rule
        assert f'ID_VENDOR_ID}}=="{spec.hardware.usb_vendor_id}"' in rule
        assert f'ID_MODEL_ID}}=="{spec.hardware.usb_product_id}"' in rule
        assert f'ID_USB_INTERFACE_NUM}}=="{spec.hardware.usb_interface_num}"' in rule
        assert ':capture:' in rule
        assert f'SYMLINK+="{spec.dev_symlink_name}"' in rule
        assert f'SYSTEMD_WANTS}}="rtp-rgb@{spec.dev_symlink_name}.service"' in rule

    def test_render_includes_port_hint_when_set(self):
        from camera_bringup.instance import (
            HardwareSpec,
            InstanceSpec,
        )
        spec = InstanceSpec(
            instance_id="test",
            dev_symlink_name="test",
            hardware=HardwareSpec(usb_port_hint="2-2"),
        )
        rule = spec.render_udev_rule()
        assert 'KERNELS=="2-2"' in rule

    def test_render_no_port_hint_when_unset(self):
        from camera_bringup.instance import (
            HardwareSpec,
            InstanceSpec,
        )
        spec = InstanceSpec(
            instance_id="test",
            dev_symlink_name="test",
            hardware=HardwareSpec(usb_port_hint=None),
        )
        rule = spec.render_udev_rule()
        assert 'KERNELS==' not in rule


class TestSpecReExports:
    """spec.py re-exports должны соответствовать active instance."""

    def test_constants_match_active_instance(self):
        from camera_bringup.spec import (
            ACTIVE_INSTANCE,
            DEV_SYMLINK,
            MIN_FIRMWARE_BCD,
            UDEV_RULE_NAME,
            USB_INTERFACE_NUM_RGB,
            USB_PRODUCT_ID,
            USB_VENDOR_ID,
            V4L2_SPEC,
        )
        assert USB_VENDOR_ID == ACTIVE_INSTANCE.hardware.usb_vendor_id
        assert USB_PRODUCT_ID == ACTIVE_INSTANCE.hardware.usb_product_id
        assert USB_INTERFACE_NUM_RGB == ACTIVE_INSTANCE.hardware.usb_interface_num
        assert MIN_FIRMWARE_BCD == ACTIVE_INSTANCE.firmware.min_bcd
        assert DEV_SYMLINK == f"/dev/{ACTIVE_INSTANCE.dev_symlink_name}"
        assert UDEV_RULE_NAME == ACTIVE_INSTANCE.udev_rule_filename
        assert V4L2_SPEC.pixel_format == ACTIVE_INSTANCE.stream.pixel_format
        assert V4L2_SPEC.width == ACTIVE_INSTANCE.stream.width
        assert V4L2_SPEC.height == ACTIVE_INSTANCE.stream.height
        assert V4L2_SPEC.fps == ACTIVE_INSTANCE.stream.fps
