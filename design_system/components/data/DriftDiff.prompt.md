**DriftDiff** — Fleet desired-vs-actual reconcile view; drift rows highlight amber so the operator previews what `Apply reconcile` would change.

```jsx
<DriftDiff
  desired={["cam55/color enabled", "cam55/depth enabled"]}
  actual={["cam55/color online", "!cam55/depth waiting_for_rtp"]}
  drift={["cam55/depth desired=active, actual=waiting_for_rtp"]}
/>
```

Notes:
- Prefix any `actual` line with `!` to flag it amber.
- An empty `drift` array renders a green "no drift · in sync" line — good for the post-reconcile state.
