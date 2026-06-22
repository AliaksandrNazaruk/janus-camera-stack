"""Tests for decide_escalation — pure decision logic, no I/O."""
from __future__ import annotations

import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SERVICE_ROOT), str(_SERVICE_ROOT.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.fdir_events import RecoveryAction
from app.services.recovery_policy import LadderLevel
from app.services.recovery_state_machine import (
    EscalationDecision,
    decide_escalation,
)


def _lvl(name: str = "x", max_attempts: int = 2, cooldown: float = 10,
         attempts: int = 0, last_attempt: float = 0.0) -> LadderLevel:
    return LadderLevel(
        name=name,
        action=RecoveryAction.NONE,
        max_attempts=max_attempts,
        cooldown_sec=cooldown,
        attempts=attempts,
        last_attempt=last_attempt,
    )


# ── exhausted ─────────────────────────────────────────────────────────

def test_exhausted_when_current_level_past_last():
    levels = [_lvl("a"), _lvl("b")]
    d = decide_escalation(
        current_level=2,  # past last
        levels=levels,
        last_escalation_ts=0,
        now=100.0,
        dedup_window_sec=3,
    )
    assert d.kind == "exhausted"
    assert d.is_actionable is True


# ── skip_dedup ────────────────────────────────────────────────────────

def test_skip_dedup_within_window():
    levels = [_lvl("a")]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=100.0,  # just happened
        now=101.0,                  # 1 sec later
        dedup_window_sec=3,         # window 3s
    )
    assert d.kind == "skip_dedup"
    assert d.level_idx == 0
    assert d.is_actionable is False  # caller simply returns status


def test_dedup_window_boundary_admits_escalation():
    """At exactly the boundary, escalation proceeds."""
    levels = [_lvl("a", cooldown=0)]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=100.0,
        now=103.0,                  # exactly dedup_window later
        dedup_window_sec=3,
    )
    assert d.kind == "execute"


# ── cooldown ──────────────────────────────────────────────────────────

def test_cooldown_blocks_when_level_recently_attempted():
    levels = [_lvl("a", cooldown=30, last_attempt=100.0)]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=0,
        now=110.0,  # 10 sec since last attempt, cooldown=30
        dedup_window_sec=0,
    )
    assert d.kind == "cooldown"
    assert d.cooldown_remaining_sec == 20.0


def test_cooldown_expired_admits_execute():
    levels = [_lvl("a", cooldown=30, last_attempt=100.0)]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=0,
        now=131.0,  # 31 sec since last attempt
        dedup_window_sec=0,
    )
    assert d.kind == "execute"


# ── escalate ──────────────────────────────────────────────────────────

def test_escalate_when_budget_exhausted():
    levels = [_lvl("a", max_attempts=2, attempts=2), _lvl("b")]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=0,
        now=100.0,
        dedup_window_sec=0,
    )
    assert d.kind == "escalate"
    assert d.level_idx == 1  # points to next level


# ── execute ───────────────────────────────────────────────────────────

def test_execute_when_all_gates_pass():
    levels = [_lvl("a", max_attempts=3, attempts=1, cooldown=10, last_attempt=10.0)]
    d = decide_escalation(
        current_level=0,
        levels=levels,
        last_escalation_ts=0,
        now=100.0,  # plenty after cooldown
        dedup_window_sec=3,
    )
    assert d.kind == "execute"
    assert d.level_idx == 0
    assert d.is_actionable is True


# ── EscalationDecision contract ───────────────────────────────────────

def test_decision_is_frozen():
    """Decision must be immutable — caller can't mutate state by accident."""
    import pytest
    d = EscalationDecision(kind="execute", level_idx=0)
    with pytest.raises(Exception):  # dataclass(frozen=True) → FrozenInstanceError
        d.level_idx = 5  # type: ignore


def test_decision_is_actionable_only_for_meaningful_kinds():
    assert EscalationDecision(kind="execute", level_idx=0).is_actionable
    assert EscalationDecision(kind="escalate", level_idx=1).is_actionable
    assert EscalationDecision(kind="exhausted", level_idx=5).is_actionable
    assert not EscalationDecision(kind="cooldown", level_idx=0).is_actionable
    assert not EscalationDecision(kind="skip_dedup", level_idx=0).is_actionable
