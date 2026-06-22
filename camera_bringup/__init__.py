"""camera_bringup — L0 layer (USB / kernel / udev / V4L2).

Этот пакет = template для per-camera instance (см. CONTRACT.md §11).
Для L1+/agent — используйте ТОЛЬКО публичные symbols ниже:

    from camera_bringup import L0, LayerStatus

См. `api.py` для quick-start примеров.
См. `CONTRACT.md` для формальной спецификации.
См. `docs/adr/` для design decisions (6 ADRs).
"""
from camera_bringup.api import (
    ALL_GUARANTEES,
    # Const tables (rarely needed in L1+)
    GUARANTEES,
    HUMAN_REQUIRED,
    # Main facade
    L0,
    CalibrationIntrinsics,
    Guarantees,
    # Typed return types
    Identity,
    # Status enum
    LayerStatus,
    RecoveryResult,
    Snapshot,
    StreamProfile,
)

__all__ = [
    "ALL_GUARANTEES",
    "GUARANTEES",
    "HUMAN_REQUIRED",
    "L0",
    "CalibrationIntrinsics",
    "Guarantees",
    "Identity",
    "LayerStatus",
    "RecoveryResult",
    "Snapshot",
    "StreamProfile",
]


__version__ = "1.0.0"
