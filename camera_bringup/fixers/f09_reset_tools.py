"""f09_reset_tools — обеспечить что L0 reset tools работают на live hardware.

С 2026-06-14 все артефакты L0-owned (см. CONTRACT.md §10):
  - hw_reset_realsense.py в camera_bringup/ (НЕ janus_camera_page/)
  - pyrealsense2 в camera_bringup/.venv (НЕ shared /opt/janus-camera-page/.venv)
  - shebang скрипта указывает на L0 venv

Что fixer делает:
  1. Создать L0 venv если не существует
  2. Install pure-Python deps (requirements.txt — currently empty marker)
  3. Install hardware deps (requirements-hardware.txt — pyrealsense2 hash-pinned)
  4. ChmodExec на скрипт

С 2026-06-15 (clean-room packaging review): pyrealsense2 split в отдельный
requirements-hardware.txt — clean-room reviewer install не triggers native
binary download (Python-version-pinned hash). Hardware path (этот fixer +
Ansible deploy) installs обе файла.

Requires root: НЕТ — venv и скрипт в boris-owned директории.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from camera_bringup.fixer import Action, Fixer
from camera_bringup.spec import (
    BRINGUP_HOME,
    HW_RESET_SCRIPT,
    L0_VENV_DIR,
    L0_VENV_PIP,
    L0_VENV_PYTHON,
)

_REQUIREMENTS_TXT = str(BRINGUP_HOME / "requirements.txt")
# Hardware-specific deps (pyrealsense2 native binary). Split из requirements.txt
# в v3.2.1 (camera_bringup/requirements-hardware.txt). Hash pinned для cp312.
_REQUIREMENTS_HARDWARE_TXT = str(BRINGUP_HOME / "requirements-hardware.txt")


class ResetToolsFixer(Fixer):
    name = "reset_tools"
    requires_root = False    # всё в L0-owned paths

    def plan(self, ctx: dict[str, Any]) -> list[Action]:
        actions: list[Action] = []

        # 1. Создать L0 venv если ещё нет (idempotent — `python3 -m venv` no-op
        # если dir уже валидный venv с тем же python)
        if not Path(L0_VENV_PYTHON).is_file():
            actions.append(Action(
                kind="run",
                description=f"python3 -m venv {L0_VENV_DIR}",
                target="python3",
                payload=f"-m venv {L0_VENV_DIR} --system-site-packages",
            ))

        # 2. Install pure-Python deps. Currently empty marker file —
        # camera_bringup package itself uses только stdlib.
        # Включаем для consistency и future-proofing (если non-binary deps
        # появятся, они попадут сюда automatically).
        actions.append(Action(
            kind="run",
            description=f"{L0_VENV_PIP} install -r requirements.txt (pure-Python)",
            target=L0_VENV_PIP,
            payload=f"install --quiet -r {_REQUIREMENTS_TXT}",
        ))

        # 3. Install hardware deps (pyrealsense2 native binary, hash-verified).
        # --require-hashes: pip отказывается ставить пакет с другим hash чем
        # в требованиях (supply chain integrity — NIST SP 800-218).
        # Wheel hash Python-version-specific — current pin matches cp312.
        actions.append(Action(
            kind="run",
            description=f"{L0_VENV_PIP} install -r requirements-hardware.txt (hash-verified)",
            target=L0_VENV_PIP,
            payload=f"install --quiet --require-hashes -r {_REQUIREMENTS_HARDWARE_TXT}",
        ))

        # 4. chmod +x на скрипт (no-op если уже)
        actions.append(Action(
            kind="chmod_exec",
            description=f"chmod +x {HW_RESET_SCRIPT}",
            target=HW_RESET_SCRIPT,
        ))

        return actions
