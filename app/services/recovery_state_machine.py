"""Recovery ladder state machine — pure transition logic.

Sprint D extraction (Phase 3): the *decision* of what to do when an
escalation request arrives — execute current level, dedup skip, wait for
cooldown, or escalate to next level — lives here as a pure function that
operates on snapshot inputs and returns an EscalationDecision value.

Why separated: previously a `while True` loop inside RecoveryLadder mixed
the decision logic with persistence, emit, and execution. Pure separation
means:
  • Decision logic testable without mocking system_mode, emit, subprocess
  • RecoveryLadder loop is straight dispatch — one branch per decision
    kind — easy to read and audit
  • Future state machine extensions (e.g. priority queues, multi-domain
    isolation) plug in here without touching execution path

The decision function does NOT mutate inputs — caller applies side effects
(increment attempts, persist state, emit events) based on decision kind.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

from app.services.recovery_policy import LadderLevel


DecisionKind = Literal[
    "exhausted",     # All levels used — caller should transition to SAFE mode
    "skip_dedup",    # Another escalation just happened — skip (no state change)
    "cooldown",      # Current level's cooldown still active — wait
    "escalate",     # Current level budget exhausted — advance to next level
    "execute",       # Conditions ok — execute current level's action
]


@dataclass(frozen=True)
class EscalationDecision:
    kind: DecisionKind
    level_idx: int
    cooldown_remaining_sec: float = 0.0

    @property
    def is_actionable(self) -> bool:
        """True if caller needs to do something beyond returning a status."""
        return self.kind in ("escalate", "execute", "exhausted")


def decide_escalation(
    *,
    current_level: int,
    levels: List[LadderLevel],
    last_escalation_ts: float,
    now: float,
    dedup_window_sec: float,
) -> EscalationDecision:
    """Pure decision: given snapshot of ladder state + clock, return what to do.

    Inputs are read-only. Caller applies side effects per the returned decision.
    """
    if current_level >= len(levels):
        return EscalationDecision(kind="exhausted", level_idx=current_level)

    if now - last_escalation_ts < dedup_window_sec:
        return EscalationDecision(kind="skip_dedup", level_idx=current_level)

    level = levels[current_level]
    if now - level.last_attempt < level.cooldown_sec:
        remaining = round(level.cooldown_sec - (now - level.last_attempt), 1)
        return EscalationDecision(
            kind="cooldown",
            level_idx=current_level,
            cooldown_remaining_sec=remaining,
        )

    if level.attempts >= level.max_attempts:
        return EscalationDecision(kind="escalate", level_idx=current_level + 1)

    return EscalationDecision(kind="execute", level_idx=current_level)
