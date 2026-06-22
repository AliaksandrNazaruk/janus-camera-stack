**AlertBar** — the global alert strip under the topbar; show the highest open severity. Alerts group by severity per the spec (critical / warning / info).

```jsx
<AlertBar severity="warning" count={1}
  message="cam55/depth waiting_for_rtp — no packets received"
  actionLabel="Open diagnostics" onAction={openDiag} />
<AlertBar severity="critical" count={2} message="Janus admin (8088) exposed on public interface" actionLabel="Fix" />
```

Notes:
- Critical=red, warning=amber, info=blue, each with a matching icon and left accent.
- Show one bar (the worst severity); link its action to the relevant Diagnostics tab.
- Needs Lucide on the page.
