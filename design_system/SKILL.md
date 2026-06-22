---
name: gateway-console-design
description: Use this skill to generate well-branded interfaces and assets for the Gateway Console (camera-fleet operator console), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, the canonical status model, action-class standard, and UI kit components for prototyping operator/diagnostics/fleet screens.
user-invocable: true
---

Read the `readme.md` file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

Key things to internalize before designing:
- This is a **fleet operations console**, not a "Janus dashboard". Operators manage nodes, streams, health, recovery, maintenance, diagnostics — never ports, mountpoints, shell commands or raw process names.
- Use the **canonical 5-family status model** (ok/warn/bad/idle/busy) via the `StatusBadge` component — never invent status colors.
- Respect the **action-class standard** (A safe → D destructive): destructive actions need a typed-phrase confirm and a remove/keep/rollback breakdown.
- **Mono for every machine value** (ids, IPs, ports, ages, hashes); IBM Plex Sans for UI; flat surfaces, hairline borders, tight radii, status-only color.

Foundations: `styles.css` (+ `tokens/`). Components: `window.GatewayConsoleDesignSystem_64aa70` (compiled to `_ds_bundle.js`). Full reference screens: `ui_kits/operator-console/`.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.
