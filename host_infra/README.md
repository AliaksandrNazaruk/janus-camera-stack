# host_infra — Ansible-based infrastructure-as-code

Управление **host OS infrastructure** (iptables, sysctl, WiFi, cron, systemd
units) которые НЕ принадлежат camera pipeline но нужны для работы стенда.

## Зачем отдельно от camera_bringup

- `camera_bringup/` (L0) = per-camera resource lifecycle
- `host_infra/` = host-wide OS state (одна шт. на host, влияет на все сервисы)

Разные scope, разные concerns. См. CONTRACT.md.

## Stack

**Ansible** (apt-installed). Industry standard для config management 1-50 hosts.
0 custom кода — используем готовые Ansible modules.

## Usage

```bash
# Verify (dry-run — что бы изменилось):
make verify

# Apply (с интерактивным confirm):
make apply

# Apply без confirm (для CI/cron):
make apply-yes

# Status (текущее состояние important sysctl/iptables):
make status
```

## Что управляется

| Role | Что |
|---|---|
| `sysctl` | `ip_forward=1`, `rp_filter=0`, **`conntrack_udp_timeout=180`** (для WebRTC) |
| `network` | iptables MASQUERADE + FORWARD для .55 → wlan0 |
| `cron` | Dedup competing NAT updaters (оставить один) |

## Что НЕ управляется (out of scope)

- Camera-specific config (см. `camera_bringup/`)
- Janus jcfg (owned by L3 = Janus)
- Docker containers (managed via docker-compose отдельно)
- Frontend / api-gateway (own pipelines)
- WiFi credentials (sensitive — manual, не в git)

## Принципы

1. **Idempotent**: повторный apply = same state
2. **Declarative**: YAML декларирует *что должно быть*, не как
3. **Verify-first**: `--check --diff` показывает drift, apply явный
4. **Безопасность ops**: ВСЕ network changes под `iptables-apply`-style
   wrapper (или с manual review), чтобы не залочить SSH
5. **Scoped к hostnames в inventory**: не распространяется на чужие хосты
