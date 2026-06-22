# P4 — declarative camera-fleet desired-state (config-as-code onboarding)

- **Status:** DESIGN. Builds on [DYNAMIC_CAMERA_ONBOARDING](DYNAMIC_CAMERA_ONBOARDING.md) (imperative add→provision→activate) + [P4_SECURITY_HARDENING](P4_SECURITY_HARDENING.md) (host-key confirm gates provisioning).
- **Node:** `.10` gateway.

## Problem
Onboarding is imperative today: `POST /nodes` → `POST /nodes/{id}/provision` → `POST /nodes/{id}/streams`, one call at a time, state lives only in the gateway store. There is no single declarative source of truth for "what cameras the fleet should have", no drift detection, and re-creating the fleet (new gateway, DR) means replaying API calls by hand.

## Design — a TOML manifest + a reconcile that converges to it
A manifest (`/etc/robot/camera-fleet.toml`, matching the existing `cam-rgb.toml` TOML convention; parsed with stdlib `tomllib`) declares the desired fleet:

```toml
# desired state — the gateway converges actual state to this
[[node]]
host = "192.168.1.55"
display_name = "front"
streams = ["color", "depth", "ir1"]   # subset of color/depth/ir1/ir2

[[node]]
host = "192.168.1.56"
display_name = "rear"
streams = ["depth", "ir1"]
```

### The creds boundary (why reconcile is two-phase)
Provisioning + activation **SSH to the node** and need the sudo password + an out-of-band-confirmed host key (P4-SEC refuses unconfirmed unless `allow_tofu`). A declarative reconcile must not bury credentials in a file. So:

- **`plan(manifest)` — read-only drift, NO creds.** For each manifest node: is it registered (store)? provisioned (`provision_state==READY`)? which desired streams already have bindings, which are missing? which *extra* streams/nodes exist (not in the manifest)? Returns a structured `FleetPlan` (per-node: `register | provision | activate[sensors] | in_sync`, plus `prune` candidates). Safe to run anytime (dashboards, CI drift checks).
- **`reconcile_gateway(manifest)` — creds-free convergence.** Performs only the gateway-side, non-SSH steps: `add_node_by_host` for missing nodes (mints node_id + per-node token + ordinal). Idempotent. Leaves provision/activate to the operator.
- **Node-side (provision + activate) stays operator-driven** via the existing creds-gated APIs — the plan tells the operator exactly which nodes need `provision` and which `streams` to activate. (A future `reconcile_full(manifest, sudo_password, allow_tofu)` could drive those too, but only with explicit creds + the host-key-confirm gate intact.)

### Pruning (declarative = converge to EXACTLY the manifest)
`plan` reports extra nodes/streams not in the manifest as `prune` candidates but **never auto-removes** — removal is destructive (tears down a live stream + its firewall rule). Pruning is an explicit, separately-confirmed action (a `--prune` flag on a future apply), never implicit in a drift check.

## Safety / non-goals
- No credentials in the manifest. The manifest is non-secret (hosts + sensor names) and can live in git.
- `plan` is pure/read-only. `reconcile_gateway` only ever *adds* gateway-side records (never removes, never SSHes).
- Does not change the allocator, FDIR, or the binding identity (serial-keyed binding_id is orthogonal — the manifest declares hosts+sensors; the gateway still mints serial-keyed binding_ids at activation).

## Tests
Manifest parse (valid/invalid sensors/missing host); `plan` classifies register/provision/activate/in_sync correctly against a seeded store; `plan` flags extra (prune) without removing; `reconcile_gateway` registers missing nodes idempotently and is a no-op when in sync; reconcile never SSHes (no transport use).
