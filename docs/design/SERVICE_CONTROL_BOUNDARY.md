# SERVICE_CONTROL_BOUNDARY — P1 recon + plan (GATED, no code yet)

Part of [STRICT_ARCHITECTURE_HARDENING.md](STRICT_ARCHITECTURE_HARDENING.md). Closes the last broad
authority leak: the L4 app shelling **`sudo -n /bin/systemctl`** (restart any unit) and **`sudo
systemctl reboot`** directly. Target: `L4 app → ServiceControlPort → scoped service-admin CLI →
systemd`, so the app's privilege is scoped to ONE binary (defense-in-depth + audit truthfulness), not
all of systemctl. Security + deployment + architecture-truthfulness boundary — the top residual risk
(user steer 2026-06-21; score capped at 8.2/10 until closed). Behavior-preserving. No code until GO.

## Recon — the two remaining broad-sudo sites (verified 2026-06-21)
1. **`services/systemd.restart_unit(unit)`** — `["sudo","-n","/bin/systemctl","restart",unit]`. Sole
   caller: `application/services_admin.restart_service` for the `("systemctl", X)` dispatch entries —
   **janus, coturn, janus-textroom-relay, janus_camera_page_hook** (the encoder units already go via
   `encoder-admin`). This is the broad `/bin/systemctl` path.
2. **`services/recovery_executor.py:247`** — `["sudo","systemctl","reboot"]` (the circuit-broken
   REBOOT_NODE ladder rung). The guard-#14 ratchet-debt entry.
   - NB: recovery_executor ALREADY uses the scoped CLIs for everything else —
     `/usr/local/bin/{encoder-admin,janus-admin,camera-admin}`. Only the reboot is still raw.

`systemd.systemctl_action` (bare `systemctl`, NO sudo) + `is_active`/`show` are a SEPARATE mechanism
(the unit runs with systemctl rights via `infrastructure/.../override.conf`); they don't use sudo and
are out of scope here (do NOT fold them in — `admin_config`'s contract, a distinct gated decision).

### The migration is already half-done at the infra level
`host_infra/roles/janus/tasks/main.yml:208-223` REMOVES the legacy broad sudoers files
(`camera-fdir` = "raw systemctl restart/start/is-active + REBOOT", `camera-janus`, `janus-camera-api`
[path-injection vuln], `rtp-rgb`) — "replaced by admin CLIs" — and 177-213 documents the intent:
**"L4 uses ТОЛЬКО /usr/local/bin/{janus,encoder}-admin."** The app-side `restart_unit`/`reboot` are the
stragglers that still assume a broad `/bin/systemctl` grant. P1 finishes this migration.

### Established scoped-CLI pattern (the template — already used 3×)
`host_infra/roles/<role>/files/<x>-admin.py` (argparse CLI, explicit allowlisted actions, exit codes)
→ deployed to `/usr/local/bin/<x>-admin` by an Ansible task that ALSO drops
`/etc/sudoers.d/<x>-admin`: `boris ALL=(root) NOPASSWD: /usr/local/bin/<x>-admin`. Existing:
`encoder-admin`, `janus-admin` (restart/nat-config/status — explicitly "scopes privilege to THIS
binary, not full systemctl"), `camera-admin` (reset-usb).

### Test oracles (the refactor must preserve / re-point these)
- `test_services_admin.py:48` `test_systemd_restart_unit_cmd_and_failure`: patches
  `systemd.subprocess.run`, calls `systemd.restart_unit("janus")`, asserts the EXACT argv
  (`sudo -n /bin/systemctl restart janus`) + RuntimeError on exec failure. → the argv assertion
  re-points to the new `service-admin` command (identical structure).
- recovery_executor's `_run_cmd` is **injected** (`run_cmd_fn`; `test_fdir_quiesce.py:112`
  `_executor(run_cmd_fn)`) — tests assert the reboot argv via the injected fn. → re-point the argv.
- `test_fdir_integration.py:459-479` asserts REMOTE node self-reboot ("systemctl reboot") — that's the
  node-agent on .10/.55, a DIFFERENT mechanism (not the gateway recovery_executor). Out of scope; leave.
- Guard **#14** (`test_architecture_fitness`): `["']systemctl["']\s*,\s*(start|stop|restart|reload|reboot)`
  allowlist = `{systemd.py, recovery_executor.py}`. After P1 routes both through `service-admin`, the
  reboot literal leaves recovery_executor → the allowlist can SHRINK (the ratchet win).

## Target shape
- **`host_infra/roles/<role>/files/service-admin.py`** — scoped CLI. `service-admin restart <unit>`
  where unit ∈ an INTERNAL allowlist {janus, coturn, janus-textroom-relay, janus_camera_page_hook}
  (defense-in-depth: the CLI refuses anything else even if sudoers were broad), and
  `service-admin reboot`. Refuses `janus-camera-page` (self). Execs `/bin/systemctl restart <unit>` /
  `/bin/systemctl reboot` as root. Exit codes mirror janus-admin.
- **Ansible task** — install it + `/etc/sudoers.d/service-admin` (`NOPASSWD: /usr/local/bin/service-admin`),
  and (separately) confirm the broad `/bin/systemctl` grant is gone.
- **`services/service_control.py`** (the ServiceControlPort) — `restart_unit(unit)` /  `reboot()` that
  shell `sudo -n /usr/local/bin/service-admin ...`. `systemd.restart_unit` delegates to it (thin
  forwarder — keeps its callers + the test seam); recovery_executor's reboot rung calls it.

## ⚠️ Deployment boundary + rollout sequencing (the gating risk)
The app change is behavior-preserving **only after the host has `service-admin` + its sudoers**. I can
AUTHOR the CLI + Ansible task + sudoers in the repo, but **I cannot deploy them to the live gateway**
(privileged, live host) — that is handed to the user (Ansible run / manual install). So the rollout is
**host-first, then app**:
1. (user) deploy `service-admin` + sudoers to `.10` (and any FDIR node that reboots).
2. (user) confirm `sudo -n /usr/local/bin/service-admin restart janus` works + `…/service-admin reboot`
   is authorized (dry-run / a safe unit).
3. (then) merge the app change routing restart/reboot through the port.
A fallback-to-`/bin/systemctl` would keep the broad path alive and defeat the security goal, so the
recommended path is NO fallback — the app change ships only once the host is ready. This is why P1 is a
"host-changes" cycle, not a pure-source refactor.

## Plan — sub-commits (tests-first; the APP commit is gated on host-ready)
1. **CLI + infra (repo artifacts, no app behavior change):** `service-admin.py` + Ansible install task +
   sudoers fragment + a unit test for the CLI's allowlist/refusal logic. Safe to land anytime (doesn't
   change the running app).
2. **char:** re-point `test_services_admin` argv assertion + the recovery reboot argv assertion to the
   `service-admin` command (RED until step 3).
3. **app (gated on host-ready):** `services/service_control.py` port; `systemd.restart_unit` →
   delegates; recovery reboot rung → `service_control.reboot()`. Suite green.
4. **guard:** shrink the guard-#14 allowlist (remove `recovery_executor.py`; and `systemd.py` if its
   raw path is gone) + optionally a new guard banning a bare `/bin/systemctl` sudo in app/** outside the
   port. The ratchet shrinks toward zero — the headline win.

## Open decisions to gate (GO before any code)
- **D1 — one `service-admin` CLI for {restart allowlisted units + reboot}** vs extend `janus-admin`
  (janus-only) + add coturn elsewhere. **Lean: one `service-admin`** (systemd service-control is its own
  concern; janus-admin stays janus/jcfg-specific).
- **D2 — app port shape:** new `services/service_control.py` ServiceControlPort that
  `systemd.restart_unit` + recovery delegate to (the user's named "ServiceControlPort"), vs just changing
  `restart_unit`'s argv in place. **Lean: the dedicated port** (names the boundary; recovery reuses it).
- **D3 — rollout:** host-first-then-app, NO `/bin/systemctl` fallback (above). **Lean: as stated** —
  step 1 (CLI+infra) lands now; step 3 (app) waits for your "host is updated" confirmation.
- **D4 — guard:** empty/shrink the #14 allowlist + add a "no bare `/bin/systemctl` sudo outside the
  port" guard. **Lean: yes** (this is the measurable closure).

## Red lines
Behavior-preserving once deployed: same units restarted, same audit strings, same RestartResponse
shape, same reboot ladder semantics. Do NOT touch the bare-`systemctl` `systemctl_action`/`is_active`
path (separate `admin_config` contract). NEVER embed the sudo password (use the scoped NOPASSWD CLI;
hand host commands to the user). The CLI itself allowlists units (defense-in-depth). `realsense_mux.py`
untouched. The app commit does not merge until the host is confirmed ready (no broad-sudo fallback).

## Status — DONE (2026-06-21)
- **Step 1** `b8740ee` — `host_infra/roles/janus/files/service-admin.py` (restart allowlist {janus,
  coturn, janus-textroom-relay, janus_camera_page_hook} + reboot; refuses self/unknown WITHOUT touching
  systemctl) + Ansible install/sudoers task + 10 shim tests. Zero app change.
- **(host)** user deployed `service-admin` + `/etc/sudoers.d/service-admin` to `.10` and confirmed.
- **Step 3** `4040b2c` — app routing (NO /bin/systemctl fallback): new
  `app/services/service_control.py` ServiceControlPort (`restart_unit` → `sudo -n
  /usr/local/bin/service-admin restart <unit>`); `services_admin` systemctl-branch → the port;
  `systemd.restart_unit` REMOVED (systemd.py now only bare-systemctl reads); recovery REBOOT_NODE rung →
  `["sudo","-n","/usr/local/bin/service-admin","reboot"]` via its existing `_run_cmd` seam. Tests
  re-pointed patch-at-source (test_services_admin + test_config_admin, identical assertions). **Guard #14
  allowlist EMPTIED** (no systemctl mutation anywhere in app/** — unconditional) + **boundary_fitness
  `_APPROVED_LEAKS` EMPTIED** + `service-admin` added to the approved-admin-CLI set. Full non-e2e green.

### Reboot deviation from the original spec
recovery uses the command-list `["sudo","-n",".../service-admin","reboot"]` via its injected `_run_cmd`
rather than a `service_control.reboot()` import — SAME scoped CLI + boundary, but it preserves recovery's
test seam (the substring reboot tests) + consistency with how it already calls encoder/janus/camera-admin.

### Remaining to fully close (host-side, user)
Verify the real path (below), then REMOVE any remaining broad `/bin/systemctl` NOPASSWD grant from
`/etc/sudoers.d/` — after that the L4 user physically cannot issue broad systemctl, only `service-admin`.
That is the final closure (lifts the 8.2 cap).
