# host_infra — Ansible-based host configuration
# Targets:
#   make verify       — dry-run, показывает что бы изменилось
#   make apply        — apply (запросит sudo password)
#   make apply-yes    — non-interactive apply (для cron/CI)
#   make status       — current sysctl + iptables snapshot
#   make lint         — ansible-lint всех playbooks

.PHONY: help verify apply apply-yes status lint syntax validate-env test

help:
	@echo "host_infra targets:"
	@echo "  verify        — dry-run (--check --diff) + validate-env"
	@echo "  apply         — apply changes (interactive sudo)"
	@echo "  apply-yes     — apply non-interactively"
	@echo "  status        — show current sysctl/iptables/env validation"
	@echo "  validate-env  — check cam env files vs group_vars (drift+sanity)"
	@echo "  test          — pytest unit tests (TURN rotator + helpers)"
	@echo "  lint          — ansible-lint (если установлен)"
	@echo "  syntax        — syntax check only"

test:
	pytest roles/janus/tests/ -v

syntax:
	ansible-playbook site.yml --syntax-check

validate-env:
	@./scripts/validate_cam_env.sh || true   # warnings non-fatal в verify

verify: syntax validate-env
	ansible-playbook site.yml --check --diff --ask-become-pass

apply: syntax
	ansible-playbook site.yml --diff --ask-become-pass

apply-yes: syntax
	ansible-playbook site.yml --diff -e "ansible_become_pass=$$CAMERA_BRINGUP_SUDO_PASS"

status:
	@echo "=== L1 sysctl (managed) ==="
	@for k in net.ipv4.ip_forward net.ipv4.conf.all.rp_filter \
	          net.ipv4.conf.br0.rp_filter \
	          net.netfilter.nf_conntrack_udp_timeout_stream \
	          net.netfilter.nf_conntrack_udp_timeout; do \
	   printf "  %-50s = %s\n" "$$k" "$$(sysctl -n $$k 2>/dev/null || echo '?')"; \
	done
	@echo ""
	@echo "=== L1 iptables NAT (POSTROUTING — наш rule) ==="
	@sudo iptables -t nat -L POSTROUTING -n 2>/dev/null | grep -E "192.168|host_infra" || echo "  (need sudo)"
	@echo ""
	@echo "=== L1 NAT updaters (должен быть ОДИН) ==="
	@echo "  systemd timer: $$(systemctl is-enabled janus-ip-refresh.timer 2>&1)"
	@printf "  cron job:      "; (sudo crontab -l 2>/dev/null | grep -i update-nat | head -1) || echo "(none)"
	@echo ""
	@echo "=== L2 encoder ==="
	@echo "  unit:          $$(systemctl is-active rtp-rgb@cam-rgb.service)"
	@echo "  snapshot:      $$(stat -c '%Y' /run/cam-rgb/snapshot.jpg 2>/dev/null | xargs -I{} expr $$(date +%s) - {} 2>/dev/null)s ago"
	@echo "  ffmpeg PID:    $$(pgrep -of 'ffmpeg.*cam-rgb' 2>/dev/null || echo '(not running)')"
	@echo "  env files:     contract=$$([ -f /etc/robot/cam-rgb.contract.env ] && echo present || echo MISSING) tuning=$$([ -f /etc/robot/cam-rgb.tuning.env ] && echo present || echo MISSING) legacy=$$([ -f /etc/robot/cam-rgb.env ] && echo PRESENT-stale || echo gone)"
	@echo ""
	@echo "=== L3 Janus ==="
	@echo "  service:       $$(systemctl is-active janus.service)"
	@echo "  HTTP REST:     $$(curl -sf --max-time 2 -X POST http://127.0.0.1:8088/janus/info -H 'Content-Type: application/json' -d '{\"janus\":\"info\",\"transaction\":\"x\"}' 2>/dev/null | head -c 50 || echo '(unreachable)')..."
	@printf "  TURN cred expiry: "; sudo grep -oE 'turn_user\s*=\s*"[0-9]+' /opt/janus/etc/janus/janus.jcfg 2>/dev/null | grep -oE '[0-9]+' | head -1 | xargs -I{} date -d @{} '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?'
	@echo ""
	@echo "=== production camera state (L0 view) ==="
	@cd /opt/janus-camera-page && python3 -m camera_bringup verify --only smoke fingerprint 2>/dev/null | grep -E "OK|WARN|FAIL"

lint:
	@which ansible-lint >/dev/null 2>&1 && ansible-lint site.yml || echo "ansible-lint not installed (apt install ansible-lint)"
