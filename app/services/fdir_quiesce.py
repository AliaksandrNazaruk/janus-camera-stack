"""TB-C1 / Track B core — the FDIR quiesce gate.

A restart makes the stream stale; without this, the autonomous watchdog re-escalates the
staleness it just caused (every ``_ESCALATION_DEDUP_SEC`` = 5 s) and climbs the recovery
ladder toward reboot. This lets a *known-disruptive* action suppress escalation for a
BOUNDED window in the affected domains. TB-C1 wires the recovery executor's own
``restart_pipeline``/``restart_janus`` (which today self-amplify); a future B2
``RESTART_ENCODER`` apply will reuse the same gate.

Safety guarantees (from the Track B v2 adversarial review):
  - **TB-C5** a HARD TTL ceiling — no caller can blind FDIR longer than
    ``QUIESCE_TTL_CEILING_SEC``; a monotonic deadline always reclaims the watchdog.
  - **TB-C6** refcount + deadline anchored to the FIRST arm — overlapping arms cannot
    walk the deadline forward; the effective window is ≤ the ceiling, not n×ttl.
  - **TB-C7** all state under one lock — no non-atomic read-modify-write.
  - **TB-C2** every suppression is observable (WARN log + an fdir event).
  - **domain-scoped** — a ``{PIPELINE, SENSOR}`` quiesce does NOT suppress a real JANUS fault.

A SEPARATE module (not ``watchdogs``) so ``recovery_executor`` can arm it without an import
cycle (``fdir_events`` is a leaf; this imports only that).
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Set

from app.services.fdir_events import Domain, RecoveryAction, Severity, emit

log = logging.getLogger(__name__)

# Hard cap (TB-C5): FDIR can never be blinded longer than this, regardless of a caller's ttl.
QUIESCE_TTL_CEILING_SEC = 120.0

_lock = threading.Lock()
_until: float = 0.0          # monotonic deadline; <= now means not quiesced
_domains: Set[Domain] = set()
_reason: str = ""
_arms: int = 0


def is_quiesced(domain: Domain) -> bool:
    """True iff a live, in-scope quiesce window covers ``domain`` right now."""
    with _lock:
        return _until > time.monotonic() and domain in _domains


def quiesce(ttl_sec: float, reason: str, domains: Set[Domain]) -> None:
    """Arm the gate (refcounted). ``ttl_sec`` is clamped to ``(0, ceiling]``. The deadline is
    set by the FIRST arm and is NOT extended by nested arms (TB-C6) — a nested arm only widens
    the suppressed domains + bumps the refcount. Pair every call with ``unquiesce``
    (use ``quiesced`` instead)."""
    if ttl_sec <= 0:
        return
    eff = min(ttl_sec, QUIESCE_TTL_CEILING_SEC)
    with _lock:
        global _until, _domains, _reason, _arms
        if _arms == 0:                      # fresh window — first arm sets the deadline
            _until = time.monotonic() + eff
            _domains = set(domains)
            _reason = reason
        else:                               # nested — widen domains, do NOT push the deadline
            _domains |= set(domains)
        _arms += 1


def unquiesce() -> None:
    with _lock:
        global _until, _domains, _reason, _arms
        _arms = max(0, _arms - 1)
        if _arms == 0:                      # last out clears authoritatively
            _until = 0.0
            _domains = set()
            _reason = ""


@contextmanager
def quiesced(ttl_sec: float, reason: str, domains: Set[Domain]):
    """Arm on enter, disarm on exit (and on exception). The monotonic deadline is the backstop
    if the block hangs/crashes without reaching the finally."""
    quiesce(ttl_sec, reason, domains)
    try:
        yield
    finally:
        unquiesce()


def note_suppressed(signal: str, domain: Domain) -> None:
    """Record a suppressed escalation (TB-C2). WARN so it survives the event ring + alert
    filters and is never mistaken for a silently-healthy stream."""
    with _lock:
        reason = _reason
        remaining = round(_until - time.monotonic(), 1)
    log.warning("FDIR escalation suppressed (planned: %s): %s [%s], %.1fs left",
                reason, signal, getattr(domain, "value", domain), remaining)
    try:
        emit(domain, Severity.WARN, detection_signal=signal,
             recovery_action=RecoveryAction.NONE, outcome="suppressed_planned",
             details={"reason": reason, "remaining_sec": remaining})
    except Exception:  # pragma: no cover — observability must never break the gate
        pass
