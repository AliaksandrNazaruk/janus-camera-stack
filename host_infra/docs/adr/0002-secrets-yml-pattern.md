# ADR 0002 — Secrets management via gitignored `secrets.yml`

**Status**: Accepted (2026-06-14)
**Context**: host_infra Ansible role needs to inject secrets (admin keys, room secrets) into templated configs (janus jcfg).

## Problem

Раньше secrets были hardcoded в роль:
- `secret: "changeme"` (mountpoint admin secret) — placeholder
- `admin_key: "stream-admin-123"` (streaming plugin admin key) — placeholder
- `secret: "$SHARED_PASS"` (textroom secret) — реюзал sudo password

Проблемы:
1. **Placeholders deployed в production** — `changeme` это сигнал что rotation never happened
2. **Password reuse** — sudo password (`$SHARED_PASS`) использовался для textroom secret → если кто-то extract'нул textroom secret из мониторинга/логов → получил sudo
3. **Невозможно committed git** — реальные secrets в репо это violation OWASP / NIST 800-218

После rotation надо где-то хранить **256-bit random values** так, чтобы:
- Ansible мог их прочитать при apply
- Git не видел plaintext
- Operator мог поменять без редактирования кода
- Schema (что должно быть) был committed

## Options considered

### A. ansible-vault encrypt-string inline (rejected for now)

```yaml
# group_vars/all.yml
janus_streaming_admin_key: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  62313365396465643834...
```

**Pros**:
- Industry standard для secret-in-git
- Schema + values вместе в одном файле
- Decryption automatic при `ansible-playbook --ask-vault-pass`

**Cons**:
- Requires vault password (where stored? `~/.ansible/vault_pass`? env var? hardware token?)
- Operator needs vault password для apply → bootstrapping problem
- Adds dependency на ansible-vault tooling
- Encrypted strings illegible в diff review (можно `ansible-vault view` но требует password)
- Overkill для текущей частоты secret rotation (раз в год)

### B. External secrets file, gitignored (accepted)

`secrets.yml` — local file с реальными values, **never committed**. `secrets.yml.example` — committed schema с `<SET-ME>` placeholders.

`site.yml` loads:
```yaml
vars_files:
  - secrets.yml
```

**Pros**:
- Zero new deps (just `vars_files` — standard Ansible)
- Operator workflow: `cp secrets.yml.example secrets.yml`, edit, apply
- Plaintext readable (no decrypt step)
- Schema (что secrets нужны) committed → onboarding документирован
- Failed apply если secrets.yml missing — explicit error не silent fallback

**Cons**:
- Operator должен sync `secrets.yml` между machines manually (rsync/scp)
- No audit log (`git log` не покажет когда какой secret rotated)
- File mode 0600 — но контроль обеспечивается операционно, не системно

### C. Pull from external secret store (rejected as over-engineered)

HashiCorp Vault, AWS Secrets Manager, etc.

**Pros**: Industry-grade audit, rotation automation, ACLs.
**Cons**: Massive overkill для single-host или small cluster. Adds runtime dep + auth setup.

## Decision

**Option B** для current scale (1-2 hosts, manual rotation cycle).

Migration path к Option A (ansible-vault) documented; switch когда:
- Number of hosts ≥ 3 (sync становится painful)
- Compliance требует audit log в git

## Implementation

### File layout

```
host_infra/
├── secrets.yml.example   # committed, schema only
├── secrets.yml           # gitignored, real values, mode 0600
└── site.yml              # vars_files: [secrets.yml]
```

### Schema (`secrets.yml.example`)

```yaml
janus_streaming_admin_key: "<SET-ME>"

cameras_secrets:
  cam-rgb: "<SET-ME>"

janus_textroom_secret: "<SET-ME>"
```

Каждый секрет имеет comment объясняющий назначение + замечание что ранее был
placeholder (`changeme` etc.) — для контекста при initial setup.

### Generate secrets

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# 43-char base64url, ~256 bits entropy
```

### Reference в template

```jinja2
{# plugin-streaming.jcfg.j2 #}
admin_key = "{{ janus_streaming_admin_key }}";
secret = "{{ cameras_secrets[name] }}";
```

Если `secrets.yml` отсутствует или missing field — apply упадёт с `'X' is undefined`.

### File permissions

`/etc/janus/janus.plugin.textroom.jcfg` deployed с mode `0640` (vs `0644` для остальных) — содержит plaintext secret, restrict от non-root readers.

## Bootstrap workflow

Новый host:

```bash
git clone ...
cd host_infra/
cp secrets.yml.example secrets.yml
chmod 600 secrets.yml
$EDITOR secrets.yml   # fill real values
make verify           # dry-run
make apply            # apply
```

## What's NOT in secrets.yml

- **Sudo password** — operator concern, не Ansible-managed
- **TURN shared secret** — в `/etc/robot/camera-secrets.env` (host-managed, read by janus-turn-rotator), не дублируем
- **MQTT/Docker secrets** — separate orchestration layer (docker-compose env)

## Verification

- Final pre-commit scan: `grep -E "<known-secret-patterns>"` against staged files (zero matches)
- Deploy verification: `sudo cat /opt/janus/etc/janus/janus.plugin.textroom.jcfg | grep secret` shows expected value
- Old secret rejected: `curl ... admin_key=stream-admin-123` → "Unauthorized code 457"
- New secret accepted: `curl ... admin_key=<NEW>` → auth passes

## References

- OWASP Secrets Management Cheat Sheet
- NIST SP 800-218 (Secure Software Development Framework) — secret handling
- ansible-vault docs: https://docs.ansible.com/ansible/latest/user_guide/vault.html
- Implementation: `host_infra/secrets.yml.example`, `host_infra/site.yml#vars_files`
