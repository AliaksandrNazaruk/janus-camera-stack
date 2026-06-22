**OperationDrawer** — the right-side drawer every mutation opens. States impact + FDIR consequence + expected duration before confirm, then streams step progress.

```jsx
<OperationDrawer
  open={open} title="Restart cam55/color" target="cam55:color"
  impactClass="B"
  impact={["Stream reconnects (5–15s)", "Viewers may briefly drop"]}
  fdirNote="stays enabled" duration="5–15s"
  steps={[
    { label: "Request sent", state: "ok" },
    { label: "Node acknowledged", state: "ok" },
    { label: "RTP resumed", state: "active" },
    { label: "Janus online", state: "pending" },
  ]}
  running onConfirm={run} onClose={close} />
```

Notes:
- `impactClass` colors the header tag and styles the confirm button: A/B primary, C warning.
- For destructive Class-D actions use **ConfirmDialog** (typed-phrase) instead of, or before, the drawer.
- Pass `result="ok"|"failed"` to switch the footer to a single Done button.
- Needs Lucide + `gc-spin` / `gc-slidein` keyframes on the page.
