# ADR 0004: Fingerprint baseline auto-accept на первом apply

## Status

Accepted (2026-06-14)

## Context

`f11_fingerprint` fixer пишет `/var/lib/camera/<instance>.json` с identity
текущей подключённой камеры. Если baseline уже существует:
- Same serial → noop (просто bump verify_count)
- Different serial → ??? (что делать?)

Два варианта:
- **A. Hard-fail**: отказаться записывать новую identity. Требует human action
  (manual delete baseline, или явный `--force`).
- **B. Auto-accept**: записать current state как новый baseline. Любое
  применение apply = «я принимаю текущую камеру как valid».

## Decision

**Approach B — auto-accept на apply.** Hard validation в check (c11), не в
fixer (f11).

Логика:
- `c11_fingerprint check` FAIL'ит при serial mismatch → видим в verify
- `f11_fingerprint fixer` принимает current как новый baseline (apply = consent)
- HUMAN_REQUIRED в api.py escalates serial mismatch как requires_human=True
  даже хотя fixer теоретически может починить

То есть **apply = explicit user act of acceptance**. Если оператор запускает
`apply --only fingerprint` после физической замены камеры — он сознательно
принимает new identity.

## Consequences

Положительные:
- **Простой mental model**: «apply фиксит drift, включая accepted swap».
- **Auto-recovery friendly**: автоматизированный pipeline может вызывать
  `attempt_recovery()` без human для firmware updates (FW drift в baseline).
- **Audit trail в fingerprint.json**: `first_seen_utc` сохраняется + `verify_count`
  — можно увидеть когда identity была установлена.

Отрицательные:
- **Possibility of silent swap acceptance**: если кто-то нажал apply «на
  автомате» после замены камеры — baseline принят без явного «yes этот swap я
  одобряю».
- Mitigation: HUMAN_REQUIRED в api.py показывает serial mismatch в
  `requires_human` даже когда fixer теоретически auto-fixable. Это помечает
  оператору: «эта проблема требует подтверждения».

## Implementation

- `f11_fingerprint.plan()` пишет current state, preserving `first_seen_utc`
- `c11_fingerprint.check()` FAIL'ит при serial mismatch
- `HUMAN_REQUIRED["fingerprint"][Status.FAIL]` = "camera identity mismatch —
  physical swap detected, confirm and re-baseline"
- `L0._collect_human_required` особо обрабатывает fingerprint FAIL —
  добавляет в requires_human даже хотя fixer существует

## Alternatives rejected

- **Hard-fail без `--force`**: ломает automated firmware-update workflow
  (legitimate FW update меняет firmware version, не serial, но всё равно
  hard-fail напрягает).
- **Whitelist serials**: даёт false sense of security — кто-то добавит и забудет
  убрать. Better — explicit per-apply consent.

## References

- `c11_fingerprint.py`, `f11_fingerprint.py`
- HUMAN_REQUIRED matrix в `api.py`
- CONTRACT.md §5 escalation matrix
