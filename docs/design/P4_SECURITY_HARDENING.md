# P4 — node control-plane hardening (per-node token + out-of-band host-key confirm)

- **Node:** `.10` gateway. Builds on [DYNAMIC_CAMERA_ONBOARDING](DYNAMIC_CAMERA_ONBOARDING.md) (P3 shipped the firewall reconciler, a *shared* node-agent token, and TOFU host-key pinning).
- **Goal:** close the two remaining control-plane auth gaps with mechanisms, not policy.

## Gap 1 — one shared `NODE_AGENT_TOKEN` for every node
Today the gateway pushes a single env token to every node and `RealNodeClient` sends that same token to every node-agent (`node_client.py:30,109`, `node_provisioner.provision(agent_token=...)`, `bootstrap.sh deploy --agent-token`). **Compromise of any one node (or its `/etc/robot/node-agent.env`) yields the token that authenticates the gateway to *all* nodes**, and a captured token is reusable across the fleet.

### Design
- **Token is per-node state, minted at enrollment.** Add `NodeEntry.agent_token` (additive, like `host_key`/`serial`). `add_node_by_host` mints `secrets.token_urlsafe(32)` on create (never on idempotent lookup). A legacy node with no token gets one minted at provision.
- **Provision pushes the node's OWN token** (from the store), not a global. `provision()` derives the token from the node row (mint-if-absent) and drops its `agent_token` parameter — the token lifecycle lives in the store, not the call site.
- **`RealNodeClient` uses the node's own token.** `get_node_client` passes `node.agent_token` (fallback to the global `NODE_AGENT_TOKEN` only for un-migrated nodes, so recovery keeps working mid-migration).
- **Rotation** = `node_provisioner.rotate_token(node_id, transport)`: mint a new token → push via a lightweight `bootstrap.sh set-token <tok>` (rewrites `node-agent.env` + restarts ONLY the agent, not the mux) → `set_agent_token` in the store. The old token dies when the agent reloads its env. Exposed as `POST /nodes/{id}/rotate-token`.
- **Blast radius after this:** a leaked token authenticates to exactly one node, and rotation is a single call.

## Gap 2 — first-contact host key is auto-TOFU'd
`provision` auto-captures the host key via `ssh-keyscan` and pins whatever it sees (`routes/stream_bindings.py:196-199`, `ssh_transport.capture_host_key` — "trusts first contact"). A MITM present at first contact is pinned permanently.

### Design — make the operator's out-of-band fingerprint the trust anchor
- **`host_key_fingerprint(known_hosts_line) -> str`** in `ssh_transport` (SHA256, the `ssh-keygen -lf` form the operator reads off the node console).
- **`POST /nodes/{id}/host-key/confirm {expected_fingerprint}`**: capture the key *fresh*, compute its fingerprint, and pin (`set_host_key`) **only if it equals the operator-supplied fingerprint** (which they obtained out-of-band, e.g. `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the node). No match → reject, nothing pinned. This is not TOFU: the operator's fingerprint is the anchor, and capturing fresh at confirm closes the capture→confirm TOCTOU.
- **`GET /nodes/{id}/host-key`**: capture + return the fingerprint the gateway sees (convenience, for the operator to compare — informational, never pins).
- **Provision refuses an unconfirmed node** unless `allow_tofu=true` (the dev/bench escape hatch — default false). So production onboarding requires an explicit out-of-band confirm; the bench can still one-shot.

## Safety / threat model
- Tokens are opaque (`secrets.token_urlsafe`), per-node, never serialized in any API response (`NodeOut` omits `agent_token`/`host_key`) and never written to the gateway log; on the node they live in `/etc/robot/node-agent.env` (`umask 077`). The node-agent compares them constant-time (`hmac.compare_digest`).
- Host-key confirm cannot *weaken* an already-pinned key: a fingerprint match against the live node is **not** enough to replace an existing pin — that requires explicit `force=true` (key rotation), audited. `provision` refuses an unconfirmed node (412) unless `allow_tofu`, which is admin-only and now **audited** (`host_key_tofu`).
- Neither path touches cam10 or any local-destructive action — this is gateway↔node control-plane auth only.

## Review findings — addressed + one known limitation
Adversarially reviewed (verdict: no auth bypass; TOFU gate + confirm-on-match sound). Addressed: the **re-pin** gap (added the `force` guard); the **empty-token masking** (the `/nodes/register` path now mints a token too, and `RealNodeClient` logs a warning whenever it falls back to the global token); **constant-time** node-agent comparison; **TOFU is now audited**.

**KNOWN LIMITATION (HIGH in a multi-user node; LOW for a single-purpose appliance):** the per-node token is passed to `bootstrap.sh deploy/set-token` as a `--agent-token <tok>` **argv** element, so it transits the node's `/proc/<pid>/cmdline` (mode 0444) for the duration of the deploy/rotate. On a dedicated camera node whose only users are `boris` (who already holds the token via the env file) + `root`, there is no separate unprivileged user to leak to, so the practical exposure is low. Proper fix (deferred — needs the SSH transport's stdin protocol changed and can't be live-verified without re-provisioning a node): deliver the token over the SSH channel's **stdin** (after the `sudo -S` password line) or via an scp'd 0600 file that `bootstrap` reads then deletes — never argv. The blast radius stays one node either way.

## Tests
Per-node token: minted on add, preserved across upsert, not re-minted on idempotent lookup; provision pushes the node's token (mint-if-legacy); `get_node_client` sends the per-node token; `rotate_token` mints+pushes+persists a new value and uses `set-token` (not a full redeploy). Host-key: fingerprint helper; confirm pins on match, rejects + leaves unpinned on mismatch; provision refuses unconfirmed unless `allow_tofu`.
