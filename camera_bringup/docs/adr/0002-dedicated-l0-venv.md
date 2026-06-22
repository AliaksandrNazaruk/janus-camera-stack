# ADR 0002: Dedicated venv для L0 (не shared с project)

## Status

Accepted (2026-06-14)

## Context

L0 нуждается в `pyrealsense2` для serial/firmware extraction. Этот пакет
изначально был установлен в shared `/opt/janus-camera-page/.venv/` который
используется janus-camera-page (L4).

Это создавало два boundary leak:
1. L0 fixer писал в shared venv (модифицировал ресурс не его)
2. Shared venv = single point of failure: если кто-то ниже по pipeline'у
   очистит venv, наш контракт рушится

## Decision

L0 получает **собственный dedicated venv**: `camera_bringup/.venv/`.

## Consequences

Положительные:
- **Clear ownership**: только L0 пишет в этот venv. Никто другой не трогает.
- **Boundary integrity**: fitness test (`test_boundary_respect`) автоматически
  проверяет что fixer не выходит за L0-owned paths.
- **Hash-pinned**: requirements.txt с SHA256 — supply chain compliance
  (NIST SP 800-218 / SLSA L2).
- **Migration safety**: если shared venv удалят/перебилдят — L0 не сломается.

Отрицательные:
- **Disk overhead**: ~50MB дополнительно (pyrealsense2 wheel + numpy).
  Acceptable на Pi5 с 64GB SD.
- **Двойная установка pyrealsense2** если L4 тоже его использует. Acceptable.

## Implementation

- `camera_bringup/.venv/` создаётся через `f09_reset_tools` fixer
  (idempotent — `python3 -m venv` no-op если уже валидный)
- `spec.py` constants: `L0_VENV_DIR`, `L0_VENV_PYTHON`, `L0_VENV_PIP`
- `hw_reset_realsense.py` shebang указывает на L0 venv
- `realsense_query.py` запускает subprocess в L0 venv

## Alternatives rejected

- **Shared venv with declared dependency**: всё ещё имеет cross-team coupling
  риск; нет защиты от очистки.
- **System python (apt install python3-pyrealsense2)**: нет .deb для D435i на
  Debian 12 Bookworm aarch64. PEP 668 блокирует system pip.
- **pipx**: для applications, не для importable libraries.

## References

- CONTRACT.md §10 migration log
- ADR 0001 (per-instance) — venv тоже мог бы быть per-instance, но overkill
