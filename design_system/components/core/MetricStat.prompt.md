**MetricStat** — a Command-Center counter: big mono numeral, uppercase label, colored status accent.

```jsx
<MetricStat label="Nodes Online" value={2} total={2} family="ok" icon="server" />
<MetricStat label="Streams Live" value={3} total={4} family="warn" icon="video" hint="cam55/depth waiting" />
<MetricStat label="Open Alerts" value={1} family="warn" hint="1 warning" icon="bell" />
```

Notes:
- `family` drives both the left accent bar and the numeral color — set it to the worst current state so the number reads red/amber when something's wrong.
- `total` renders a faint `/N` denominator for "live out of configured" counts.
- `icon` is a Lucide name (needs Lucide on the page).
