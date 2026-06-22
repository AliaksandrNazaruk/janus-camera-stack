# ADR 0001: Per-instance pattern over parameterized monolith

## Status

Accepted (2026-06-14)

## Context

L0 нужно поддерживать N камер (потенциально). Возможны два подхода:

**A. Parameterized monolith**: один L0 module знает про все камеры. Все API
принимают `camera_id` параметр. Central registry с массивом cameras.

**B. Per-instance pattern**: L0 = template. Каждая физическая камера = instance
этого template с собственным конфигом, fingerprint, lock, udev rule. На уровень
выше — fleet manager оркестрирует instances.

## Decision

**Approach B — per-instance.**

## Consequences

Положительные:
- **No big refactor**: текущий L0 не меняется, просто становится «one of many
  instances». Контракт v1 не сломан.
- **Independent failure domains**: bug в L0 не affects все instance одновременно.
  Cam A падает — cam B продолжает.
- **systemd template alignment**: `rtp-rgb@<instance>.service` pattern уже
  существует в нашем стеке — multi-instance L0 органично туда вписывается.
- **Container-friendly**: каждый instance = potential docker container later.
- **Industry alignment**: NVIDIA DeepStream (pipeline per camera), ROS 2
  (namespace per camera) — это standard pattern.

Отрицательные:
- **Disk overhead**: per-instance fingerprint, lock, udev rule файлы (~kB each).
  Acceptable.
- **No cross-instance atomic ops**: нельзя одной командой restart всё. Required
  выше — fleet manager территория.
- **Discriminator complexity для D435i**: USB iSerial=0 → нужен
  `usb_port_hint` в TOML для udev. Не идеально, но реалистично.

## Implementation

- `instance.py` с `InstanceSpec` dataclass + TOML loader
- `instances/<id>.toml` для каждой instance
- `spec.py` загружает active instance при import (через
  `CAMERA_BRINGUP_INSTANCE` ENV или CLI `--instance`)
- Per-instance defaults: fingerprint `/var/lib/camera/<id>.json`, lock
  `/run/camera_bringup-<id>.lock`, udev rule `99-<id>.rules`

## Alternatives rejected

- **A. Parameterized monolith**: cross-camera bug surface, breaking refactor,
  central registry — overkill для 1-5 camera typical robot setup.
- **Hybrid (central registry + per-instance state)**: лишний слой, добавляет
  только complexity.

## References

- CONTRACT.md §11 (multi-instance pattern)
- NVIDIA DeepStream Reference App design notes
- ROS 2 lifecycle nodes documentation
