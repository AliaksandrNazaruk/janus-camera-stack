**NodeCard** — full management card for a physical camera node; local and remote nodes use the same layout (only the left accent differs: blue=local, slate=remote).

```jsx
<NodeCard
  nodeId="cam55" host="192.168.1.55" role="remote_producer"
  model="RealSense D435" serial="141722072135" status="online"
  health={{ agent: "online", camera: "present", lastSeen: "8s ago",
            provision: "ready", maintenance: "off", hostKey: "pinned", token: "present" }}
  streams={[
    { sensor: "color", status: "online", mp: 2000, port: 5100, rtpAge: "90ms" },
    { sensor: "depth", status: "stale",  mp: 2001, port: 5102, rtpAge: "24s" },
  ]}
/>
```

Notes:
- Destructive actions (Remove / Deprovision / Forget host key) live behind the collapsed **Danger Zone** toggle — never inline with safe actions.
- Health values auto-color through `statusFamily`; `maintenance:"on"` shows busy/blue.
- `local` flips the accent to blue and shows a "local" tag.
