"""Domain exceptions for the sensor pipeline lifecycle.

A separate base module so the encoder-admin adapter (which raises LifecycleError) does not have to
import the orchestrator — avoids an import cycle within the package (Phase 4 / A-04).
"""
from __future__ import annotations


class UnsupportedSensor(Exception):
    pass


class LifecycleError(RuntimeError):
    pass
