**StatusBadge** — the single status pill for the console; pass a raw gateway state string and it auto-colors into one of five families (ok/warn/bad/idle/busy).

```jsx
<StatusBadge state="online" />
<StatusBadge state="waiting_for_rtp" />
<StatusBadge state="stale" size="sm" />
<StatusBadge state="provisioning" pulse />
<StatusBadge family="ok" label="synced" />
```

Notes:
- State mapping is canonical (§18): `online/synced/healthy`→ok, `degraded/waiting/drift`→warn, `failed/stale/critical`→bad, `offline/disabled/unknown`→idle, `provisioning/recovering/maintenance`→busy. Unknown strings fall back to `idle`.
- Sizes: `sm` (in dense tables/chips), `md` (default), `lg` (card headers).
- `pulse` animates the dot — reserve for genuinely live/recovering states (needs the `gc-pulse` keyframe; included in cards).
- `statusFamily(state)` is exported for coloring non-badge elements (accent bars, dots) consistently.
