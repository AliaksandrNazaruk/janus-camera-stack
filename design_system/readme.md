# Gateway Console — Design System

The design system for the **Camera Gateway Operator Console**: an operator
control surface for a fleet of WebRTC camera nodes. It is deliberately *not* a
"Janus dashboard" — it is a **fleet operations console** where an operator
manages **nodes, streams, health, recovery, maintenance and diagnostics**, not
ports, mountpoints, shell commands or raw process names.

> Target feeling: *"Fleet operations console for camera nodes."*
> The operator should never have to think *"which mountpoint? which RTP port?
> local or remote? which systemctl?"* — only *"cam55/color online · rtp 90ms ·
> FDIR enabled · Open viewer · Stop stream."*

---

## Sources

- **Codebase:** `janus_camera_page` (attached at `janus_camera_page-e011dcc-20260619/janus_camera_page/`).
  A reusable WebRTC video-streaming stack (V4L2 / RealSense / RTSP → Janus →
  browser). Existing operator surfaces read for visual language:
  `templates/console.html`, `templates/camera_hosts.html`,
  `templates/devices_dashboard.html`, and the stylesheets
  `static/css/console.css` + `static/css/camera_config.css`.
- **Specification:** *Future UI Specification — Camera Gateway Operator Console*
  (pasted by the user). Defines the 7-section IA, the status model, the action
  design standard (Class A–D), the alert/audit models, the onboarding wizard and
  the `/api/v1/ui/*` view-model contracts. This design system implements that spec.

The original console is a functional slate+blue admin UI built on system fonts.
This system **carries its palette forward** (slate neutrals, signal blue,
semantic green/amber/red/gray) and **elevates** it into a dense, industrial
operator console with a formal five-family status model and IBM Plex typography.

### Domain vocabulary (use these words, exactly)
`Gateway` (central `.10` node) · `Node` (local `.10` or remote `.55`) ·
`Camera` (USB/RealSense device) · `Stream` (`color`/`depth`/`ir1`/`ir2`) ·
`Binding` (Node/Sensor → RTP → Janus mountpoint → Viewer) · `FDIR` (autonomous
recovery) · `Fleet` (desired vs actual) · `Operation` (any operator action).

---

## CONTENT FUNDAMENTALS

How the console talks. Copy is **operator-facing, terse, and technical** — it
respects that the reader is an engineer under time pressure.

- **Voice:** imperative and declarative, never chatty. Labels are verbs
  (`Restart`, `Stop`, `Provision`, `Dry-run`, `Apply`, `Rotate token`) or bare
  nouns (`Streams`, `Nodes`, `Last seen`). No "please", no marketing, no
  exclamation.
- **Person:** second-person is implied, never written ("Open viewer", not "Open
  your viewer"). The system describes state in third person ("cam55/depth
  waiting_for_rtp", "FDIR skipped — maintenance on").
- **Casing:** Title Case for nav + section + card titles ("Command Center",
  "Attention Required"). `UPPERCASE` + letter-spacing for micro metric labels
  ("NODES ONLINE", "LAST SEEN"). lowercase for status badge text (`online`,
  `waiting_for_rtp`) — badges echo the raw machine state verbatim.
- **Machine values stay raw.** Never prettify an id, state or path. It's
  `cam55:color`, `waiting_for_rtp`, `mp 2000`, `192.168.1.10:5100`, `rtp_age
  90ms`, `SHA256:8d:f2:…` — shown in **mono**, unchanged. This is a trust signal:
  the UI shows the operator exactly what the system sees.
- **Numbers are concrete and unit-bearing.** `80ms`, `24s`, `3/4`, `8s ago`,
  `5–15s`. Ages auto-color (fresh green → stale red). Avoid vague words like
  "recently" when a number exists.
- **Impact is always spelled out** before a risky action: "stream may go offline
  for 5–20 seconds · FDIR will be disabled · viewers may reconnect." Destructive
  actions list *what will be removed*, *what will stay*, and the *rollback path*.
- **Emoji:** none. The source UI used 📷/📺/⚠ in nav; the elevated console
  replaces these with line icons. Emoji are not part of this brand.
- **Tone examples (verbatim spirit):**
  `cam55/depth waiting_for_rtp · last error: no packets received` ·
  `FDIR skipped cam55 because maintenance on` ·
  `Type cam55 to confirm` · `Janus HTTP 8088 is exposed`.

---

## VISUAL FOUNDATIONS

The console reads as **engineered, dense, and calm** — instrument-panel, not
web-app. Everything earns its place; status is the loudest thing on screen.

- **Color.** Slate neutral spine (`--slate-50…950`) + a single **signal blue**
  (`--blue-600`) for primary action, selection and "local node". All other color
  is **status**, never decoration. Five canonical families (see Status model):
  ok=green, warn=amber, bad=red, idle=gray, busy=blue. There are no brand
  gradients, no purple, no accent rainbow. Imagery (video tiles) is shown on
  **near-black** (`--slate-950`) — the only dark surfaces in the content area.
- **Chrome vs content.** The sidebar + footer are **dark** (`--slate-900`,
  mission-control rail); the content area is **light** (`--slate-50` page,
  white cards). This split orients the operator instantly.
- **Type.** `IBM Plex Sans` for all UI; `IBM Plex Mono` for **every
  machine-emitted value** (ids, IPs, ports, ages, hashes, timestamps,
  config). 13px base — dense. Big counts are mono numerals. (The source used the
  system-ui stack; IBM Plex is the elevation — see Caveats.)
- **Spacing.** 4px grid (`--space-*`). Tight, consistent gutters (12–16px between
  cards). Layout via flex/grid + `gap`, never margin-stacking. Max content width
  1320px, centered.
- **Corners.** Tight and engineered: 4px buttons/inputs, 6px chips, 8px cards,
  12px drawers/dialogs, pill only for status badges. Nothing is "soft".
- **Borders over shadows.** Surfaces in the document plane are defined by 1px
  hairline borders (`--border-subtle`/`--slate-200`), not lift. **Shadows are
  reserved for floating layers** (drawers, dialogs, dropdowns, the role-switch
  thumb). A 3px **left accent bar** flags identity/severity on cards (blue=local
  node, slate=remote node, status-color on health/metric cards).
- **Cards.** White background, 1px subtle border, 8px radius, optional 3px left
  accent. No drop shadow at rest. Header row (title + right-aligned actions) over
  a hairline divider, then padded body.
- **Status badge.** Pill, lowercase label echoing the raw state, optional leading
  dot in the family's solid color. One component (`StatusBadge`) owns the
  state→color mapping so color is always consistent.
- **Backgrounds.** Flat. `--slate-50` app canvas, white cards, `--slate-100`
  sunken wells/code blocks, near-black video tiles. No textures, patterns,
  illustrations or hero imagery — this is an instrument, not a marketing page.
- **Animation.** Minimal and functional. `120ms` ease on hover/background;
  drawers slide in (`gc-slidein`, ~180ms), dialogs pop (`gc-pop`, ~160ms),
  spinners (`gc-spin`) and a single status `gc-pulse` for genuinely live states.
  No bounce, no parallax, no decorative loops.
- **Hover / press.** Hover = a step up the slate scale (`--surface-hover`) or the
  darker action blue (`--action-hover`); ghost buttons gain a faint slate wash.
  Press is immediate color change, no scale/shrink. Focus = 3px blue ring
  (`--shadow-focus`).
- **Transparency / blur.** Used only for overlay scrims (`--surface-overlay`,
  ~55% near-black) with a 1px backdrop blur behind drawers/dialogs. Never on
  content surfaces.
- **Density.** High. Tables and rows are the primary layout; an operator should
  read system health in under 10 seconds and open any stream in one click.

---

## ICONOGRAPHY

- **System:** [Lucide](https://lucide.dev) — a clean, consistent **line** icon
  set (~1.75px stroke, rounded joins) that matches the engineered, low-noise
  aesthetic. Loaded from CDN (`unpkg.com/lucide@latest`); components accept a
  Lucide name via the `icon` prop and render `<i data-lucide="…">`, resolved by
  `lucide.createIcons()`.
- **Why a substitution:** the source codebase had **no icon assets** — it used a
  handful of emoji (📷 📺 ⚠) and a `/favicon.ico` reference in nav text. There
  were no SVGs, no icon font, and no logo/illustration files to copy. Lucide is
  the closest faithful, free, line-based substitute for an operator console.
  **If you have a house icon set, swap it in** (see Caveats).
- **Usage:** icons are **functional, monochrome, and paired with text** — they
  never carry meaning alone. Status is communicated by the `StatusBadge`
  dot/color, not by icon choice. Common glyphs: `server` (nodes), `layers`
  (streams), `monitor-play` (viewer), `stethoscope` (diagnostics/check),
  `activity` (FDIR), `shield-check`/`triangle-alert`/`octagon-alert` (health &
  alert severity), `rotate-cw` (restart), `square` (stop), `trash-2` (remove).
- **Emoji:** not used. **Unicode** is used sparingly as glyph affordances only
  (`·` separators, `→`/`↔` in plans/flows, `✓` in the wizard stepper).
- **Brand mark:** there is no source logo. The sidebar uses a generated wordmark
  — a `cctv` Lucide glyph in a blue rounded square + "GATEWAY / CONSOLE"
  lockup. Treat it as a placeholder (see Caveats).

---

## Status model (canonical)

Every node / stream / FDIR / firewall state maps to exactly one family. The
mapping lives in `StatusBadge` (`statusFamily(state)`), so color is consistent
everywhere.

| Family | Color | States |
|---|---|---|
| `ok` | green | online · synced · healthy · ready · active · enabled · present · valid · pinned |
| `warn` | amber | degraded · waiting · waiting_for_rtp · drift · pending · warning |
| `bad` | red | failed · stale · critical · stopped · unreachable · blocked · apply_failed |
| `idle` | gray | offline · disabled · unknown · configured_offline · off · unset |
| `busy` | blue | provisioning · recovering · maintenance · host_key_pending · suppressed |

Action classes (drive confirmation + button color): **A** safe read-only (no
confirm) · **B** reversible (log) · **C** service-impacting (confirm + impact) ·
**D** destructive (typed-phrase confirm + remove/keep/rollback).

---

## Index / manifest

**Foundations**
- `styles.css` — entry point (import manifest only)
- `tokens/colors.css` · `tokens/typography.css` · `tokens/spacing.css` ·
  `tokens/status.css` · `tokens/fonts.css`
- Specimen cards in `guidelines/` (Colors · Type · Spacing)

**Components** (`window.GatewayConsoleDesignSystem_64aa70`)
- `components/core/` — **StatusBadge** (+ `statusFamily`), **ActionButton**, **MetricStat**
- `components/data/` — **HealthCard**, **NodeCard**, **StreamRow**, **EventTimeline**, **DriftDiff**, **ViewerTile**, **DiagnosticsPanel**
- `components/feedback/` — **AlertBar**
- `components/overlay/` — **OperationDrawer**, **ConfirmDialog**
- Each directory has a `.card.html` (Design System tab), and each component has `.d.ts` + `.prompt.md`.

**UI kit**
- `ui_kits/operator-console/` — full click-through console (7 sections, drawers,
  dialogs, onboarding wizard). See its `README.md`.

**Other**
- `SKILL.md` — Agent-Skill entry point for downloading/using this system.

> `DangerButton` from the spec's component list is realized as
> `ActionButton variant="danger"` / `"danger-solid"` rather than a separate
> component. `OperationDrawer` covers the spec's `OperationDrawer`;
> `ConfirmDialog` covers Class-C/D confirmations.
