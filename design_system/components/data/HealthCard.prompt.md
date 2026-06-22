**HealthCard** — the Command-Center "System Status" strip; answers "is everything healthy?" at a glance. The left accent auto-reflects the worst service state.

```jsx
<HealthCard
  title="System Status"
  services={[
    { name: "Gateway", status: "healthy" },
    { name: "Janus", status: "healthy" },
    { name: "Cloudflare", status: "healthy" },
    { name: "FDIR", status: "enabled" },
    { name: "Firewall", status: "synced" },
    { name: "Streams", status: "degraded", label: "3/4 live" },
  ]}
/>
```

Notes:
- Needs Lucide on the page for the shield icon.
- Use `label` for compound values like "3/4 live" while still passing a `status` for color.
