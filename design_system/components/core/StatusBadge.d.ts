import * as React from "react";

export type StatusFamily = "ok" | "warn" | "bad" | "idle" | "busy";
export type StatusSize = "sm" | "md" | "lg";

/**
 * The single status primitive for the operator console. Pass a raw gateway
 * state string and it auto-resolves to one of five color families with a dot.
 *
 * @startingPoint section="Core" subtitle="Auto-colored status pill for any node/stream state" viewport="700x150"
 */
export interface StatusBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Raw state string, e.g. "online", "waiting_for_rtp", "stale". Auto-mapped. */
  state?: string;
  /** Override the rendered text (defaults to a de-underscored `state`). */
  label?: string;
  /** Force a family, bypassing auto-mapping. */
  family?: StatusFamily;
  /** Visual size. Default "md". */
  size?: StatusSize;
  /** Show the leading status dot. Default true. */
  dot?: boolean;
  /** Pulse the dot — use for live/recovering states only. Default false. */
  pulse?: boolean;
}

export function StatusBadge(props: StatusBadgeProps): JSX.Element;

/** Resolve a raw state string to its status family. */
export function statusFamily(state?: string): StatusFamily;
