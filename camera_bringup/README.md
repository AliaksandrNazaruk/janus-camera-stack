# camera_bringup

L0 = USB / kernel / udev / V4L2 layer для color камеры на узле .10.
Это самый нижний слой стека (см. STACK_NODE10_RESEARCH.md).

> **Формальный контракт слоя**: [CONTRACT.md](CONTRACT.md) — что L0 обещает
> вышестоящим, что требует, какие failure modes.
>
> **Programmatic API**: [api.py](api.py) — `from camera_bringup.api import L0`.
>
> **CLI**: `python3 -m camera_bringup verify|apply` (см. ниже).
>
> Status: **11 checks / 4 fixers / 12 guarantees / 189 tests** работают идемпотентно
> с:
> - concurrent-safe apply (flock per-instance)
> - TTL-кэшированным status()
> - Prometheus метриками (camera_bringup_l0_status, check_status, guarantee, ...)
> - structured JSON logging с trace_id
> - HMAC-signed fingerprint baseline (tamper detection)
> - factory calibration (intrinsics для color/depth/IR) в baseline
> - F-isolation SAFE mode (operator-controlled quarantine)
> - per-instance pattern (1 template → N camera instances)
> - SystemPort skeleton для testable refactoring
> - 6 ADRs документирующих key decisions

## Что делает

Проверяет 10 аспектов нижнего слоя:

| Check | Проверяет |
|---|---|
| `usb_enumerate` | Камера 8086:0b3a подключена, sysfs path, USB speed (USB2 vs USB3) |
| `usb_power` | power/control, autosuspend, persist, runtime_status соответствуют спеке |
| `uvcvideo` | Модуль загружен; runtime quirks/timeout/nodrop совпадают с modprobe.d |
| `udev` | 99-cam-rgb.rules установлен, совпадает с fixture; нет конфликтующих legacy |
| `dev_symlinks` | `/dev/cam-rgb` ведёт на правильный RGB-capture node |
| `v4l2` | V4L2 умеет YUYV 640x480@15fps; capture 3 кадров (или BUSY) |
| `firmware` | bcdDevice ≥ MIN_FIRMWARE_BCD |
| `bandwidth` | Текущий профиль из cam-rgb.env влезает в USB capacity |
| `reset_tools` | usbreset, authorize, hw_reset_realsense.py + pyrealsense2 — все доступны |
| `smoke` | ffmpeg+libx264 есть, rs-stream.sh есть, rs-stream@color.service loaded |
| `fingerprint` | serial+FW+USB-path совпадают с baseline в `/var/lib/camera/fingerprint.json` (детектор «камеру подменили») |

## Использование

### Programmatic API (для L1+/agent)

Top-level import:
```python
from camera_bringup import L0, LayerStatus, StreamProfile, CalibrationIntrinsics
```

**Quick start:**
```python
# 1. Готова ли L0?
if not L0.is_ready():
    raise CameraNotReady(L0.requires_human())

# 2. Параметры стрима для encoder'a
profile = L0.stream_profile()
spawn_ffmpeg(device=profile.device_path, **profile.encoder_kwargs())

# 3. Calibration для CV
cal = L0.calibration("color")
K = cal.to_camera_matrix()   # 3x3 numpy-ready

# 4. Granular guarantee check
if not L0.guarantees().USB_POWER_LOCKED_ON:
    log.warning("autosuspend risk")

# 5. Diagnostics snapshot
log.info("camera state", extra={"l0": L0.snapshot().to_dict()})

# 6. Auto-recovery
result = L0.attempt_recovery()
if result.needs_attention:
    alert(result.failed_fixers, result.requires_human)
```

**Full match-based status handling:**
```python
match L0.status():
    case LayerStatus.HEALTHY:
        start_pipeline()
    case LayerStatus.DRIFTED:
        L0.attempt_recovery()
    case LayerStatus.DEGRADED:
        log.warn("suboptimal: %s", L0.requires_human())
        start_pipeline()
    case LayerStatus.SAFE:
        log.info("L0 quarantined, idle")
    case _:   # BROKEN | UNKNOWN
        raise CameraNotReady(L0.requires_human())
```

См. [CONTRACT.md §4](CONTRACT.md) — полный API reference + typed shapes.

### Verify (audit, без побочных эффектов)

```bash
# Полный audit (human-readable)
python3 -m camera_bringup verify

# JSON output (для агента / CI)
python3 -m camera_bringup verify --json

# Только конкретные checks
python3 -m camera_bringup verify --only usb_power udev

# Список всех checks
python3 -m camera_bringup list
```

### Apply (фиксеры — лечит детектированный drift)

```bash
# Что было бы сделано (dry-run, безопасно):
python3 -m camera_bringup apply --dry-run

# Реально применить (нужен sudo если хоть один fixer requires_root):
sudo python3 -m camera_bringup apply --yes-root

# Точечно — только конкретные fixers:
sudo python3 -m camera_bringup apply --only usb_power

# Продолжать со следующим fixer'ом если первый упал:
sudo python3 -m camera_bringup apply --yes-root --continue-on-fail

# JSON output (для агента):
sudo python3 -m camera_bringup apply --yes-root --json
```

### Семантика apply

1. **Pre-check**: запускает соответствующий check. Если OK → `SKIPPED`, ничего не делает.
2. **Plan**: возвращает список action'ов. С `--dry-run` — только печатает, не выполняет.
3. **Execute**: выполняет actions по одному, fail-fast на первой ошибке.
4. **Backup**: каждый `write_file` копирует prev-content в `.pause-state/backup/`.
5. **Post-check**: пере-запускает check. Если OK → `APPLIED`, иначе `UNFIXED`.

Statuses fixer'а:
- `SKIPPED` — check уже OK, fixer не нужен (или dry-run)
- `APPLIED` — actions выполнены, post-check показал OK (drift закрыт)
- `UNFIXED` — actions ok, но post-check всё ещё показывает drift (что-то ещё)
- `FAILED` — какой-то action упал (см. action.error)

Exit codes apply:
- `0` — все SKIPPED или APPLIED
- `1` — есть UNFIXED
- `2` — есть FAILED

### Exit codes

- `0` — все OK или WARN (система работает или просто не идеально)
- `1` — есть FAIL (что-то сломано или критично не настроено)
- `2` — есть ERROR (баг в самом check, не в системе)

## Структура

```
camera_bringup/
├── README.md                — этот файл
├── spec.py                  — все эталонные значения (single source of truth)
├── check.py                 — базовый CheckResult/Status/runner/printer
├── fixer.py                 — Fixer ABC, Action types, runner для apply
├── cli.py                   — entry point, argparse (verify + apply + list)
├── __main__.py              — позволяет `python -m camera_bringup`
├── checks/
│   ├── __init__.py          — registry всех checks (порядок выполнения)
│   ├── c01_usb_enumerate.py
│   ├── c02_usb_power.py
│   ├── c03_uvcvideo.py
│   ├── c04_udev.py
│   ├── c05_dev_symlinks.py
│   ├── c06_v4l2.py
│   ├── c07_firmware.py
│   ├── c08_bandwidth.py
│   ├── c09_reset_tools.py
│   └── c10_smoke.py
├── fixers/
│   ├── __init__.py          — registry (1:1 mapping check → fixer)
│   ├── f02_usb_power.py     — install udev rule + reload + trigger + settle
│   ├── f09_reset_tools.py   — pip install pyrealsense2 + shebang + chmod
│   └── f11_fingerprint.py   — write /var/lib/camera/fingerprint.json baseline
├── realsense_query.py       — subprocess в .venv для serial+FW через pyrealsense2
├── checks/c11_fingerprint.py — сравнивает current vs baseline
├── fixtures/
│   ├── 99-cam-rgb.rules     — каноническое udev правило (для check'а udev)
│   └── uvcvideo.conf        — каноническое modprobe.d
└── .pause-state/
    ├── pause.sh             — pause prod stack (sudo)
    ├── resume.sh            — resume prod stack (sudo)
    ├── backup/              — auto-backup от каждого write_file action
    └── udev_backup_*/       — manual backup'ы при ручных правках
```

## Принципы

1. **Read-only**. Verify никогда не меняет систему. Можно безопасно запустить
   во время работы камеры — verify ничего не разорвёт.
2. **Идемпотентность**. Повторный запуск с тем же state = тот же результат.
3. **Машинно-читаемый output**. `--json` для использования будущим агентом.
4. **Fail-fast на критичных, warn на советах**. Различие FAIL vs WARN
   осмысленное — FAIL значит «не работает», WARN значит «работает, но
   надо подкрутить».
5. **Single source of truth — spec.py**. Никаких «волшебных чисел» в checks.
   Все эталоны там.

## Что НЕ делает (намеренно)

- Не запускает ffmpeg для sustained capture (конфликтует с running rs-stream@color)
- Не делает USB reset, hw_reset, modprobe reload (это apply, не verify)
- Не трогает Janus, FastAPI, frontend (это слои выше — L3+)
- Не проверяет network / NAT / TURN / cloudflared (это L1, но другая
  подсистема — будет отдельный bringup)

## Контракт стабильности `/dev/cam-rgb` (alias)

Камера сейчас доступна через **5 параллельных symlink'ов** к одному и тому же
`/dev/videoN`. Не все из них стабильны:

| Symlink | Источник | Стабилен после reboot | Стабилен после replug в **тот же** порт | Стабилен после replug в **другой** порт |
|---|---|---|---|---|
| `/dev/video4` (kernel-assigned N) | kernel enumeration order | ❌ N может поменяться | ❌ N может поменяться | ❌ N + sysfs_path меняется |
| `/dev/cam-rgb` | наш `99-cam-rgb.rules` (vendor+product+interface+capability) | ✅ | ✅ | ✅ |
| `/dev/video-realsense-rgb` | legacy `99-realsense.rules` (vendor+product, без interface фильтра) | ⚠️ часто но не гарантированно | ⚠️ | ⚠️ |
| `/dev/v4l/by-id/usb-Intel_R..._index0` | kernel `60-persistent-v4l.rules` (по USB string descriptor) | ✅ для одного D435i | ✅ | ✅ |
| `/dev/v4l/by-path/platform-...usb-0:2:1.3-...` | kernel `60-persistent-v4l.rules` (по USB порту) | ✅ | ✅ | ❌ путь меняется |

**Какой использовать**:
- В **ffmpeg pipeline** (`rs-stream.sh`): `/dev/cam-rgb` — порт-независимый, контролируемый, документированный
- В **systemd unit BindsTo**: `dev-cam-rgb.device` (производное от `/dev/cam-rgb`)
- В **диагностике**: смотреть `udevadm info -q symlink -n /dev/video4` чтобы видеть всё дерево

### Что покрывает наш udev rule

Match'ит на **камеру**, не **порт**:
```
SUBSYSTEM=="video4linux", KERNEL=="video*",
  ENV{ID_VENDOR_ID}=="8086",            ← Intel
  ENV{ID_MODEL_ID}=="0b3a",             ← D435i
  ENV{ID_USB_INTERFACE_NUM}=="03",      ← RGB sensor (не depth/IR/control)
  ENV{ID_V4L_CAPABILITIES}==":capture:",← только capture node
  SYMLINK+="cam-rgb",                   ← стабильный alias
  ENV{SYSTEMD_ALIAS}="/dev/cam-rgb",
  TAG+="systemd"                        ← dev-cam-rgb.device unit
```

> Note: udev rule больше **не** автостартит encoder (был
> `SYSTEMD_WANTS=rtp-rgb@cam-rgb.service`). Lifecycle стрима теперь
> reconciler/dashboard-driven через `sensor-reconcile.service`, а не udev.

Что переживёт:
- ✅ Reboot — udev перезапустится на boot, rule сработает, symlink создастся
- ✅ Replug в любой USB порт — rule match по vendor/product/interface, не по порту
- ✅ Kernel rotation `/dev/videoN` — symlink всегда правильный
- ✅ Concurrent enumeration с другими USB устройствами — rule специфичен (interface_num=03)

Что НЕ покрывает:
- ❌ **Две D435i одновременно** — наш rule match'ит обе → race за `/dev/cam-rgb`.
  `usb_enumerate` check вернёт FAIL в этом случае. Для multi-camera нужны
  serial-based aliases (отдельный TODO, потребует udev `RUN+=` хелпера с
  pyrealsense2 для serial extraction, т.к. USB iSerial у D435i = 0).
- ❌ **Подмена на другую D435i** — alias тот же. Защита — `fingerprint` check
  (sравнит serial с baseline и FAIL'ит).

### USB autosuspend recovery после disconnect

При физическом disconnect+reconnect:
1. Kernel замечает disappear → удаляет `/dev/videoN`
2. `BindsTo=dev-cam-rgb.device` в `rs-stream@color.service` → systemd останавливает unit
3. Kernel при reconnect создаёт новый `/dev/videoN` (возможно другой номер)
4. Udev rule срабатывает → создаёт `/dev/cam-rgb` симлинк (autostart больше
   нет — udev только восстанавливает symlink + `dev-cam-rgb.device`)
5. `sensor-reconcile.service` (reconciler/dashboard-driven) реконсилит
   желаемое состояние и поднимает `rs-stream@color.service` обратно

**Это recovery path не тестировался физически** (см. known issues — replug
test недоступен в remote режиме). Но `udevadm test` подтверждает что правила
matched бы корректно на add event.

## Observability

### Prometheus metrics

```bash
python3 -m camera_bringup metrics > /var/lib/node_exporter/textfile/camera_bringup.prom
```

Эту команду рекомендуется запускать через cron каждые 30-60s. node_exporter с
`--collector.textfile.directory=...` подхватит файл автоматически.

Метрики:
- `camera_bringup_l0_status{status}` — one-hot LayerStatus (healthy/drifted/degraded/broken/unknown)
- `camera_bringup_check_status{check, status}` — one-hot per-check (11×5 строк)
- `camera_bringup_check_duration_seconds{check}` — длительность последнего запуска check
- `camera_bringup_guarantee{name}` — 0/1 per guarantee (12 guarantees)
- `camera_bringup_camera_info{serial, firmware, product, usb_type}` — info-style identity
- `camera_bringup_collection_timestamp_seconds` — когда собрано

### Structured JSON logging

Все check/fixer events эмитятся в stderr как JSON Lines.

```bash
# По умолчанию JSON в stderr:
python3 -m camera_bringup verify 2>&1 > /dev/null | jq

# Human-readable:
CAMERA_BRINGUP_LOG_FORMAT=human python3 -m camera_bringup verify

# Tracing chain (override correlation_id):
CAMERA_BRINGUP_TRACE_ID=abc123 python3 -m camera_bringup verify
```

Каждый log entry содержит: `ts, level, logger, trace_id, msg, ...extras`.

## Concurrency & safety

- **flock** на apply: одновременно может работать только один `apply`. Concurrent
  попытка возвращает `FixerStatus.FAILED` с `LockBusyError`. Lock в
  `/run/camera_bringup.lock` (override через `CAMERA_BRINGUP_LOCK_FILE`).
- **TTL cache** для `L0.status()`: 5s по умолчанию (override через
  `CAMERA_BRINGUP_STATUS_TTL_S`). Invalidate после apply автоматически.
- **Verify полностью read-only**: проверено fitness test'ом (`test_no_side_effects`).

## SAFE mode (F-isolation, ECSS-Q-ST-30)

Operator-controlled quarantine. Apply заблокирован пока SAFE active.
Verify работает (read-only).

```bash
# Войти в SAFE mode (например, перед заменой камеры):
sudo python3 -m camera_bringup safe enter --reason "swapping camera"

# Статус (exit 0 = в SAFE, 1 = не в SAFE — удобно для cron):
python3 -m camera_bringup safe status

# Выйти:
sudo python3 -m camera_bringup safe exit
```

Marker файл: `/var/lib/camera/<instance>.safe` (JSON с reason + timestamp + set_by).
Survives reboot — выход только явный.

## Calibration data (для CV pipeline)

`fingerprint.json` baseline теперь содержит **factory intrinsics** от
pyrealsense2 для всех sensor (color/depth/infrared):

```json
"calibration": {
  "color":    {"fx": 1356.88, "fy": 1356.64, "ppx": 959.13, "ppy": 559.20,
               "width": 1920, "height": 1080,
               "model": "inverse_brown_conrady", "coeffs": [0,0,0,0,0]},
  "depth":    {"fx": 639.13, "fy": 639.13, ..., "model": "brown_conrady"},
  "infrared": {"fx": 639.13, "fy": 639.13, ..., "model": "brown_conrady"}
}
```

c11_fingerprint детектирует calibration drift (например после firmware
update). 3D reconstruction pipeline может читать intrinsics напрямую
из baseline для undistortion/back-projection.

## HMAC-signed baseline (tamper detection)

Secret в `/etc/camera_bringup/secret.key` (mode 600). Baseline подписан
HMAC-SHA256. Подделка `fingerprint.json` (даже root) детектируется:

- `c11_fingerprint` валидирует `_hmac` field; mismatch → FAIL
- Migration: первое apply создаёт secret + перепишет baseline с подписью
- Backward compat: baseline без `_hmac` legacy mode (no validation)

## Ports/adapters pattern (incremental)

`SystemPort` protocol в `ports.py`:
- `RealSystemPort` для production
- `FakeSystemPort` для unit tests (no monkeypatch boilerplate)
- Reference migration: `c02_usb_power` принимает `system: SystemPort` kwarg
- Остальные 10 checks мигрируют incrementally on-touch (см. ADR 0006)

## ENV overrides (12-factor)

| ENV | Default | Что |
|---|---|---|
| `CAMERA_BRINGUP_ROBOT_HOME` | `/opt/janus-camera-page` | Корень проекта |
| `CAMERA_BRINGUP_HOME` | `$ROBOT_HOME/camera_bringup` | Корень L0 |
| `CAMERA_BRINGUP_FINGERPRINT` | `/var/lib/camera/fingerprint.json` | Baseline path |
| `CAMERA_BRINGUP_FINGERPRINT_DIR` | `/var/lib/camera` | Baseline dir |
| `CAMERA_BRINGUP_UDEV_DIR` | `/etc/udev/rules.d` | udev rules dir |
| `CAMERA_BRINGUP_MODPROBE_CONF` | `/etc/modprobe.d/uvcvideo.conf` | modprobe config |
| `CAMERA_BRINGUP_DEV_SYMLINK` | `/dev/cam-rgb` | symlink path |
| `CAMERA_BRINGUP_LOCK_FILE` | `/run/camera_bringup.lock` | flock path |
| `CAMERA_BRINGUP_STATUS_TTL_S` | `5.0` | Status cache TTL |
| `CAMERA_BRINGUP_LOG_LEVEL` | `INFO` | logging level |
| `CAMERA_BRINGUP_LOG_FORMAT` | `json` | json/human |
| `CAMERA_BRINGUP_TRACE_ID` | (auto-generated UUID) | для tracing chain |
| `CAMERA_BRINGUP_INSTANCE` | `cam-rgb` | active instance ID (см. instances/) |
| `CAMERA_BRINGUP_INSTANCES_DIR` | `<HOME>/instances` | dir with TOML instance configs |
| `CAMERA_BRINGUP_HMAC_SECRET` | `/etc/camera_bringup/secret.key` | HMAC secret для fingerprint |

## Supply chain

L0 runtime deps split (с 2026-06-15, clean-room packaging review):
- `requirements.txt` — pure-Python (currently empty; package uses только stdlib)
- `requirements-hardware.txt` — `pyrealsense2` native binary, hash-pinned для cp312:

```
pyrealsense2==2.58.1.10581 \
    --hash=sha256:1226bb06e6965cb9edb2c3d477be2ddadfcb6f60be2f2ec8649c23b19800752b
```

Установка делается fixer'ом `f09_reset_tools` (оба файла):
```bash
camera_bringup/.venv/bin/pip install -r requirements.txt
camera_bringup/.venv/bin/pip install --require-hashes -r requirements-hardware.txt
```

Pip отказывается ставить пакет с другим hash. Соответствует NIST SP 800-218 / SLSA L2.

Clean-room reviewer / CI environments install только `requirements.txt`
(no native binary download attempted → reproducible across Python versions).

## Tooling & quality gates

### Linting + formatting

```bash
# Scoped к camera_bringup (НЕ распространяется на соседние сервисы):
bash camera_bringup/scripts/lint.sh

# Auto-fix:
bash camera_bringup/scripts/lint.sh --fix
```

ruff config в `pyproject.toml`. Включены: pycodestyle, pyflakes, isort, bugbear,
comprehensions, pyupgrade, ruff-specific. Игнорят: RUF001-003 (false positive
для русских docstrings).

### Type checking

```bash
camera_bringup/.venv/bin/python -m mypy camera_bringup/ --config-file camera_bringup/pyproject.toml
```

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install --config camera_bringup/.pre-commit-config.yaml
```

Hooks: ruff (scoped), mypy, fitness tests, SBOM regen на изменения requirements.

### Coverage

```bash
pytest camera_bringup/tests/ --cov=camera_bringup --cov-branch --cov-report=html \
    --cov-config=camera_bringup/pyproject.toml
```

Current: **76% branch coverage** (threshold: 75%, CI fails ниже).

### Property-based tests

```bash
pytest camera_bringup/tests/unit/test_property_based.py -v
```

Hypothesis generates тысячи random inputs для `_derive_status`, `_compare`,
HMAC `sign/verify` round-trips. Finds edge cases которые example-based tests
не покрывают.

### Performance benchmarks

```bash
camera_bringup/.venv/bin/python -m pytest \
    camera_bringup/tests/unit/test_benchmarks.py --benchmark-only

# Compare against saved baseline:
... --benchmark-autosave
... --benchmark-compare
```

Current targets (all pass):
- `_derive_status` pure: < 100 µs (actual: 1.6 µs)
- `L0.status()` cached: < 1 ms (actual: 24 µs)
- `L0.guarantees()` cached: < 5 ms (actual: 6.7 µs)
- `L0.snapshot()`: < 250 ms (actual: 190 ms, dominated by pyrealsense2 subprocess)

### Supply chain security

```bash
# Generate SBOM (Software Bill of Materials):
bash camera_bringup/scripts/generate_sbom.sh

# Audit для known CVEs:
bash camera_bringup/scripts/audit_deps.sh
```

Hardware dependencies pinned + hash-verified в `requirements-hardware.txt`
(NIST SP 800-218 / SLSA L2). Pure-Python `requirements.txt` currently имеет no
runtime deps — package uses только stdlib. См. "Supply chain" section выше для
split rationale.

## Known issues

- **`firmware` check** меряет USB `bcdDevice` (5100 → "51.00"), а это **не**
  firmware version. Реальная firmware D435i `5.16.0.1` доступна только через
  `pyrealsense2`. См. `fingerprint` который правильно её получает. Чек надо
  переделать на pyrealsense2-based — TODO. Сейчас он валидирует только
  что `bcdDevice` не нулевой и не запредельно старый.

- **Reboot test НЕ проведён** — мы работаем удалённо, риск unbootable системы.
  Все наши apply-изменения теоретически survive reboot (udev rule загружается
  при boot, pyrealsense2 живёт в .venv независимо), но эмпирически не проверено.

- **Replug test НЕ проведён** — по той же причине. Удев rule `ACTION=="add|change"`
  должен сработать на replug, но physically untested.

- **`--only X` для check с ctx-зависимостью** (например fingerprint без
  usb_enumerate) даст partial diff потому что `ctx['sysfs_path']` пуст.
  Полный verify всегда корректен. Можно обойти: `--only usb_enumerate fingerprint`.

## Testing

Покрытие на 3 уровнях. Запуск всего:
```bash
cd /opt/janus-camera-page
python3 -m pytest camera_bringup/tests/ -v
# 86 tests, ~14 секунд на Pi 5
```

### Unit (47 тестов)
Чистая логика без IO: parsers, state machine derive, comparisons. Запускаются всегда.
```bash
python3 -m pytest camera_bringup/tests/unit/
```
Покрывают:
- `_derive_status` — все 5 переходов state machine (HEALTHY/DRIFTED/DEGRADED/BROKEN/UNKNOWN)
- `c11._compare` — все варианты diff (serial/vendor/firmware/sysfs)
- modprobe parser, udev rule normalizer, shebang extractor, bcd decoder
- `CheckResult`, `safe_run`, `exit_code` semantics

### Integration (18 тестов)
Реальная система с реальной камерой. Auto-skip если D435i не подключена.
```bash
python3 -m pytest camera_bringup/tests/integration/ -m integration
```
Покрывают:
- L0 API на живой системе (status, identity, postconditions, summary)
- CLI verify / apply --dry-run / list

### Fitness (12 тестов)
Архитектурные инварианты — ловят drift между контрактом и реализацией.
```bash
python3 -m pytest camera_bringup/tests/fitness/ -m fitness
```
Покрывают:
- **Conformance**: каждая GUARANTEES.{name} ссылается на существующий check; fixer.name = key in ALL_FIXERS = check name; HUMAN_REQUIRED references valid checks
- **Documentation drift**: CONTRACT.md упоминает все 12 GUARANTEES и все 5 LayerStatus
- **State machine coverage**: для каждой комбинации (Status×Status) _derive_status возвращает валидный LayerStatus
- **No side effects**: verify и dry-run apply НЕ модифицируют файлы (compared via mtime)
- **Idempotency**: повторный apply на чистом state = все SKIPPED

### Markers

- `@pytest.mark.integration` — auto-skipped если нет D435i
- `@pytest.mark.fitness` — архитектурные тесты, дёшевы, должны всегда pass
- `@pytest.mark.destructive` — модифицируют систему, требуют явного включения

```bash
# CI без железа:
python3 -m pytest -m "not integration"

# Только архитектурные (быстро):
python3 -m pytest -m fitness

# Только unit:
python3 -m pytest camera_bringup/tests/unit/
```

## Зависимости

Системные:
- `lsusb` (usbutils)
- `udevadm` (systemd)
- `v4l2-ctl` (v4l-utils)
- `ffmpeg`
- `python3.9+` (без сторонних libs кроме stdlib)

Опциональные:
- `usbreset` (для reset_tools check)
- `pyrealsense2` (для hw_reset_realsense.py)

## Будущее

- Расширение fixer coverage (для остальных checks которые могут drift'ить —
  udev, uvcvideo, dev_symlinks, smoke)
- `fingerprint` команда — сохранение serial/FW/USB path в
  `/var/lib/camera/fingerprint.json` (детектор «камеру подменили»)
- `status` — компактный одностраничный дашборд
- Аналогичный bringup для слоя L2 (encoder/ffmpeg/rs-stream unit) — отдельный пакет
- Аналогичный bringup для других камер (depth на .55) — добавление camera_id
  параметра в CLI

## Pause / Resume helpers (для apply работы)

Если apply нужен в момент когда камера активно стримит, можно временно
заморозить стек:

```bash
sudo bash /opt/janus-camera-page/camera_bringup/.pause-state/pause.sh
# ... apply work ...
sudo bash /opt/janus-camera-page/camera_bringup/.pause-state/resume.sh
```

Останавливаются только: `rs-stream@color`, `janus-camera-page`,
`janus_camera_page_hook`. Не трогаются: `janus.service` (для .55 path),
`cloudflared`, `camera-nat`, Docker stack.
