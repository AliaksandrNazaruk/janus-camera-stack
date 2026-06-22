# Operator Console — UI kit

A high-fidelity, click-through recreation of the **Camera Gateway Operator
Console**: the elevated "fleet operations console" the spec calls for, built
entirely from this design system's components.

## Run
Open `index.html`. It loads the compiled `_ds_bundle.js` (two levels up) and
the global stylesheet, then mounts the console.

## Files
- `index.html` — app shell + routing + operation/confirm wiring + Add-node wizard
- `shell.jsx` — `Sidebar`, `Topbar` (dark chrome, role switcher, breadcrumb)
- `screens.jsx` — the 7 sections, keyed on `window.SCREENS`
- `fleet-data.js` — mock view-model mirroring `/api/v1/ui/*` (`window.FLEET`)

## What's interactive
- **Sidebar nav** switches between all 7 sections.
- **Command Center** → Restart / Diagnostics / Maintenance from "Attention Required" and the live-streams table.
- **Streams table** → Restart opens an **OperationDrawer** (impact → confirm → step progress); Stop opens a Class-C **ConfirmDialog**.
- **Nodes** → Provision / Maintenance / Rotate open operation drawers; **Danger Zone → Remove node** opens a Class-D typed-phrase confirm; **Add node** opens the 6-step wizard.
- **Viewer Wall** → 1-up / 2-up / 4-up layout toggle with pinned tiles + FDIR badges.
- **Diagnostics** → tabbed (Overview / Node / Stream / Firewall / FDIR events / Audit).
- **Topbar** → role switcher (Operator / Engineer / Admin) and a spinning refresh.

## Notes
- This is a recreation for design reference — actions are simulated client-side,
  no backend calls. Local (`cam10`) and remote (`cam55`) nodes render identically,
  per the spec's hard requirement.
- Composes design-system primitives only — it does not re-implement them.
