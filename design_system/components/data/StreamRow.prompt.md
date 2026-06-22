**StreamRow** — one row of the operator Streams table; local and remote streams look identical. Renders a `<tr>`, so mount inside `<table><tbody>` with a matching header.

```jsx
<table style={{ width: "100%", borderCollapse: "collapse" }}>
  <tbody>
    <StreamRow binding="cam10:color" node="cam10" sensor="color"
      status="online" rtpAgeMs={80} mountpoint={1305} rtpPort={5004} fdir="enabled" />
    <StreamRow binding="cam55:depth" node="cam55" sensor="depth"
      status="stale" rtpAgeMs={24000} mountpoint={2001} rtpPort={5102}
      fdir="disabled" lastError="no RTP" />
  </tbody>
</table>
```

Notes:
- `rtpAgeMs` auto-colors: <1s green, <5s amber, ≥5s red.
- Primary actions are built in (Open / Restart / Stop or Diagnose). Stop shows for live-ish states; Diagnose replaces it when the stream isn't carrying.
- Pair with the column header: Stream · Node · Sensor · Status · RTP Age · Janus MP · RTP Port · FDIR · Last Error · Actions.
