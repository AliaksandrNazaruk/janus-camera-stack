# ADR 0006: Ports/adapters pattern — incremental migration

## Status

Accepted (2026-06-14) — skeleton + reference migration (c02_usb_power).
Остальные 10 checks мигрируют **инкрементально** at-touch-time.

## Context

Checks делают direct system calls (`open()`, `subprocess.run()`,
`glob.glob()`, `os.path.realpath()`). Unit tests используют
`monkeypatch.setattr(...)` для substitution.

Pain points:
- Monkeypatch boilerplate (~5-10 строк setup per test)
- Не явно где seam: нужно знать какую функцию подменять
- Tests могут случайно дёрнуть real subprocess если забыли mock
- Сложно гарантировать что check не делает unexpected IO

Standard solution: **ports/adapters pattern** (hexagonal architecture).
Check'и зависят от `SystemPort` protocol, не от concrete `os`. Production
получает `RealSystemPort`, тесты — `FakeSystemPort`.

## Decision

1. Создать `ports.py` с `SystemPort` protocol + `RealSystemPort` + `FakeSystemPort`
2. Migrate **один check (c02_usb_power)** as reference implementation
3. Документировать pattern (этот ADR)
4. **НЕ форсировать** migration остальных 10 checks сразу

Migration трigger: когда касаешься check'а по делу (баг, новая фича) —
заодно мигрируешь на SystemPort. Иначе оставляешь как есть.

## Consequences

Положительные:
- **Seam определена**: новые checks (если появятся) пишутся сразу с SystemPort
- **Pattern документирован**: ADR + working example (c02 + test_ports.py)
- **No breaking changes**: 173 existing tests продолжают работать
- **Optional migration**: команда может мигрировать на своих условиях

Отрицательные:
- **Inconsistency**: c02 использует SystemPort, c01/c03-c11 — нет
- **Two patterns в codebase**: monkeypatch vs FakeSystemPort
- **Migration debt**: 10 checks "ждут своего часа"

## Why not full migration?

Full refactor = ~8 часов работы с риском сломать 173 tests. Tradeoff:
- **Cost**: high (8h + risk)
- **Value**: medium (cleaner code, easier future check additions)
- **YAGNI weight**: high — у нас уже работающие unit tests через monkeypatch

Решение: skeleton + reference + plan migration on-touch. Если через 3 месяца
больше половины checks мигрировано — стоит дофиксить остальные одним PR.

## Implementation

```python
# ports.py
class SystemPort(Protocol):
    def read_file(self, path: str) -> Optional[str]: ...
    def exists(self, path: str) -> bool: ...
    def glob(self, pattern: str) -> List[str]: ...
    def run(self, cmd: List[str], *, timeout: float = 10) -> RunResult: ...

class RealSystemPort:   # production
    ...

@dataclass
class FakeSystemPort:   # tests
    files: Dict[str, str]
    run_responses: Dict[tuple, RunResult]
    run_history: List[List[str]]  # для assertions
```

```python
# c02_usb_power.py (reference migration)
def check(ctx, system: SystemPort = None) -> CheckResult:
    system = system or default_system()
    ...
    control = system.read_file(f"{sysfs_path}/power/control")
    ...
```

```python
# test_ports.py — стиль для будущих tests
fake = FakeSystemPort(files={"/sys/.../control": "on", ...})
result = check({...}, system=fake)
assert result.status == Status.OK
```

## Migration checklist (для будущего check migration)

1. Добавить `system: Optional[SystemPort] = None` параметр к `check()`
2. `system = system or default_system()` в начале
3. Заменить `open()` → `system.read_file()`
4. Заменить `subprocess.run()` → `system.run()`
5. Заменить `glob.glob()` → `system.glob()`
6. Заменить `os.path.exists()` → `system.exists()`
7. Старые unit tests с `monkeypatch` оставить (backward compat)
8. Добавить новые tests через FakeSystemPort
9. После N миграций — удалить monkeypatch-based tests

## Alternatives rejected

- **Full upfront refactor**: 8h, no new functionality, breaking risk
- **Don't introduce SystemPort**: monkeypatch продолжает работать, но
  scaling unclear если checks 30+
- **Use mock.patch instead of FakeSystemPort**: mock.patch теряет
  type safety и encourages over-mocking

## References

- `ports.py` — SystemPort implementation
- `checks/c02_usb_power.py` — reference migration
- `tests/unit/test_ports.py` — test pattern demo
- Cockburn "Hexagonal Architecture" (2005)
