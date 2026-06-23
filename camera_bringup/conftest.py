"""Root-level conftest для tests/.

Делает две вещи:
  1. Добавляет parent dir в sys.path чтобы `import camera_bringup` работал,
     когда pytest запускается из camera_bringup/ напрямую.
  2. Регистрирует общие fixtures (см. tests/conftest.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

# parent of camera_bringup/ = /home/boris/robot/
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
