**ActionButton** — the console's button primitive; `variant` encodes the action's risk class so destructive actions always read as dangerous.

```jsx
<ActionButton variant="primary" icon="play">Open viewer</ActionButton>
<ActionButton variant="default" icon="rotate-cw">Restart</ActionButton>
<ActionButton variant="ghost" icon="refresh-cw" size="sm">Refresh</ActionButton>
<ActionButton variant="warning" icon="square">Stop stream</ActionButton>
<ActionButton variant="danger" icon="trash-2">Remove node</ActionButton>
<ActionButton busy>Applying…</ActionButton>
```

Notes:
- Map to action classes: A/B (safe/reversible) → `default`/`ghost`/`primary`; C (service-impacting) → `warning`; D (destructive) → `danger` or `danger-solid`.
- Sizes `xs`/`sm` for table-row and chip actions, `md` default, `lg` for wizard CTAs.
- `icon`/`iconRight` take Lucide names; include `<script src="https://unpkg.com/lucide@latest">` + `lucide.createIcons()` on the page.
- `busy` swaps in a spinner and blocks clicks (needs `gc-spin` keyframe).
