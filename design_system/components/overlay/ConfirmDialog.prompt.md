**ConfirmDialog** — confirmation modal for Class C (service-impacting) and Class D (destructive) actions; Class D requires typing an exact phrase before confirm unlocks.

```jsx
// Class C
<ConfirmDialog open={open} title="Stop cam55/color"
  message="Stopping disables FDIR for this binding."
  impact={["Stream goes offline immediately", "FDIR will be disabled", "Viewers reconnect on restart"]}
  confirmLabel="Stop stream" onConfirm={stop} onClose={close} />

// Class D
<ConfirmDialog open={open} title="Remove node cam55" destructive
  confirmPhrase="cam55"
  willRemove={["cam55:color binding + mp 2000", "cam55:depth binding + mp 2001", "firewall rules → 192.168.1.55"]}
  willKeep={["Gateway cam10 + its streams", "Audit history"]}
  rollback="Re-onboard via the Add Node wizard."
  confirmLabel="Remove node" onConfirm={remove} onClose={close} />
```

Notes:
- The confirm button stays disabled until the typed phrase matches `confirmPhrase` exactly.
- `destructive` switches to red `danger-solid` confirm; otherwise warning/amber.
- Needs Lucide on the page.
