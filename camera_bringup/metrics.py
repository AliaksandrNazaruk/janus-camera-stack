"""Prometheus metrics export для L0.

Использование (cron каждый минут или каждые 5 мин):
    sudo python3 -m camera_bringup metrics > /var/lib/node_exporter/textfile/camera_bringup.prom

Затем node_exporter --collector.textfile.directory=/var/lib/node_exporter/textfile
автоматически подхватит метрики и отдаст в Prometheus.

Метрики:
  camera_bringup_l0_status{status="<name>"}      — gauge 0/1, одно значение active
  camera_bringup_check_status{check, status}     — gauge 0/1, по 5 строк на check
  camera_bringup_guarantee{name}                 — gauge 0/1
  camera_bringup_check_duration_seconds{check}   — gauge, время последнего run
  camera_bringup_fingerprint_serial{serial,fw}   — info-style (always 1)
  camera_bringup_layer_collection_timestamp      — gauge unix ts
"""
from __future__ import annotations

import time

from camera_bringup.api import L0, LayerStatus
from camera_bringup.check import Status
from camera_bringup.checks import ALL_CHECKS


def _escape_label_value(s: str) -> str:
    """Prometheus label values: escape backslash, quote, newline."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _gauge_lines(name: str, help_text: str, samples: list[tuple]) -> list[str]:
    """Generate Prometheus textfile lines for one gauge metric."""
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for labels_dict, value in samples:
        labels = ",".join(
            f'{k}="{_escape_label_value(str(v))}"'
            for k, v in labels_dict.items()
        )
        out.append(f"{name}{{{labels}}} {value}")
    return out


def collect() -> str:
    """Полный snapshot всех метрик L0 в Prometheus textfile format."""
    # Pre-collect: один проход чтобы все метрики были consistent
    L0.invalidate_cache()
    summary = L0.summary()
    postconds = L0.postconditions()
    identity = L0.identity() or {}

    # Per-check status + durations
    per_check_samples = []
    per_check_durations = []
    layer_status = summary["status"]
    ctx: dict = {}
    for name, fn in ALL_CHECKS:
        t0 = time.monotonic()
        from camera_bringup.check import safe_run
        r = safe_run(name, fn, ctx)
        duration = time.monotonic() - t0
        # one-hot encoded: 1 for current status, 0 for others
        for status_enum in Status:
            per_check_samples.append(({
                "check": name,
                "status": status_enum.value,
            }, 1 if r.status == status_enum else 0))
        per_check_durations.append(({"check": name}, f"{duration:.6f}"))

    # Layer status (one-hot encoded)
    layer_samples = []
    for s in LayerStatus:
        layer_samples.append(({"status": s.value}, 1 if layer_status == s.value else 0))

    # Guarantees
    guarantee_samples = []
    for guarantee_name, ok in postconds.items():
        guarantee_samples.append(({"name": guarantee_name}, 1 if ok else 0))

    # Identity (info-style metric — always 1)
    identity_samples = []
    if identity.get("serial"):
        identity_samples.append(({
            "serial": identity.get("serial", ""),
            "firmware": identity.get("firmware", ""),
            "product": identity.get("name", ""),
            "usb_type": identity.get("usb_type", ""),
        }, 1))

    # Build output
    sections = [
        _gauge_lines(
            "camera_bringup_l0_status",
            "L0 layer status (one-hot encoded)",
            layer_samples,
        ),
        _gauge_lines(
            "camera_bringup_check_status",
            "Per-check status (one-hot encoded)",
            per_check_samples,
        ),
        _gauge_lines(
            "camera_bringup_check_duration_seconds",
            "Last execution duration of each check, seconds",
            per_check_durations,
        ),
        _gauge_lines(
            "camera_bringup_guarantee",
            "L0 guarantee satisfied (1=true)",
            guarantee_samples,
        ),
    ]
    if identity_samples:
        sections.append(_gauge_lines(
            "camera_bringup_camera_info",
            "Connected camera identity (info-style)",
            identity_samples,
        ))

    # Timestamp
    sections.append(_gauge_lines(
        "camera_bringup_collection_timestamp_seconds",
        "Unix timestamp когда metrics были собраны",
        [({}, int(time.time()))],
    ))

    return "\n".join("\n".join(s) for s in sections) + "\n"
