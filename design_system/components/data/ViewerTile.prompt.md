**ViewerTile** — one tile of the Viewer Wall: a 16:9 video surface with status overlay, pin toggle, and quick-action footer (Restart / Diag / Fullscreen).

```jsx
<ViewerTile binding="cam55:color" status="online" rtpAge="90ms" pinned
  media={<video src="…" autoPlay muted playsInline style={{ width: "100%", height: "100%", objectFit: "cover" }} />} />
<ViewerTile binding="cam55:depth" status="stale" rtpAge="24s" fdirEvent="restart" />
```

Notes:
- Without `media` a dark placeholder frame renders — fine for mockups.
- `pinned` outlines the tile blue; `fdirEvent` shows a blue badge over the video.
- Tiles are dark by design (mission-control video wall); lay them out in a CSS grid (1-up / 2-up / 4-up).
- Needs Lucide on the page.
