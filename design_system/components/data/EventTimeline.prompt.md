**EventTimeline** — chronological FDIR / audit / activity feed with a status-colored rail dot per entry.

```jsx
<EventTimeline events={[
  { time: "14:22", target: "cam55:color", message: "restarted by operator", result: "ok", actor: "operator", action: "stream.restart" },
  { time: "14:20", target: "cam55", message: "FDIR skipped — maintenance on", result: "suppressed", reason: "maintenance" },
  { time: "14:15", message: "firewall reconcile applied", result: "ok" },
]} />
```

Notes:
- Color comes from `result`/`level` via `statusFamily`, or set `family` directly.
- `dense` tightens vertical rhythm for sidebars / drawer Activity tabs.
- For an audit log, pass `action` + `actor`; for FDIR events pass `target` + `reason`.
