**DiagnosticsPanel** — a titled key/value block for the Diagnostics screens (Agent, Camera, Services, Data-plane, Control-plane). Values render in mono; pass `status` to color a row as a state.

```jsx
<DiagnosticsPanel title="Data plane" icon="radio" rows={[
  { key: "RTP packets", value: "yes", status: "ok" },
  { key: "rtp_age_ms", value: "90" },
  { key: "janus_video_age", value: "100ms" },
  { key: "webrtc_viewers", value: "1" },
]} />
```

Notes:
- A `status` on a row colors the value and adds a dot — use it for booleans/health, leave it off for raw numbers.
- Needs Lucide for the header icon.
