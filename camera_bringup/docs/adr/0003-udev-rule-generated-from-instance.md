# ADR 0003: udev rule generated from InstanceSpec, не static fixture

## Status

Accepted (2026-06-14)

## Context

Изначально udev rule был статическим fixture: `fixtures/99-cam-rgb.rules`.
c04_udev check сравнивал installed rule с fixture для drift detection.

Когда добавился multi-instance pattern (ADR 0001), стало невозможно держать
fixture per-instance — пришлось бы N fixture файлов, manually synced с TOML.

Кроме того, `usb_port_hint` в InstanceSpec (для discriminator между несколькими
D435i) требует динамической генерации `KERNELS=="<port>"` clause.

## Decision

udev rule **генерируется** через `InstanceSpec.render_udev_rule()` метод.
Static fixture `fixtures/99-cam-rgb.rules` удалён.

c04_udev check сравнивает installed file с output `render_udev_rule()` от
ACTIVE_INSTANCE.

f04_udev fixer пишет output `render_udev_rule()` в `/etc/udev/rules.d/`.

## Consequences

Положительные:
- **Single source of truth**: TOML файл instance. udev rule auto-derived.
- **No fixture/code sync issue**: изменение TOML автоматически даёт правильный
  rule.
- **Multi-instance support**: каждая instance генерирует свой rule с
  правильным port_hint.
- **Тестируемость**: `render_udev_rule()` — pure function, легко тестировать.

Отрицательные:
- **Less inspectable as static file**: чтобы увидеть какой rule будет
  установлен, нужно вызвать `render_udev_rule()`. Mitigation: fitness test
  + apply --dry-run показывает diff.
- **render() bugs могут породить нерабочий rule**: mitigation — fitness test
  проверяет наличие критичных match tokens (SUBSYSTEM, KERNEL, ENV vendor/product).

## Implementation

```python
# instance.py
class InstanceSpec:
    def render_udev_rule(self) -> str:
        port_match = f'KERNELS=="{self.hardware.usb_port_hint}", ' if self.hardware.usb_port_hint else ""
        return f"""
SUBSYSTEM=="video4linux", KERNEL=="video*", \\
  ENV{{ID_VENDOR_ID}}=="{self.hardware.usb_vendor_id}", ENV{{ID_MODEL_ID}}=="{self.hardware.usb_product_id}", ...
  {port_match}\\
  SYMLINK+="{self.dev_symlink_name}", ...
"""
```

Fitness test (`test_render_includes_required_match_tokens`) проверяет что
generated rule имеет все обязательные match tokens.

## Alternatives rejected

- **Jinja2 templates**: лишняя dependency для одной функции.
- **Per-instance fixture + macro substitution**: всё ещё N файлов, ничего не
  упрощает.

## References

- ADR 0001 (per-instance pattern)
- `tests/fitness/test_instance_consistency.py::TestUdevRuleGeneration`
