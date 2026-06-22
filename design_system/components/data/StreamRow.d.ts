import * as React from "react";

/**
 * One row of the Streams table. Local and remote streams render identically.
 * Renders as a `<tr>` — use inside a `<table><tbody>` with a matching header.
 *
 * @startingPoint section="Data" subtitle="Operator stream table row with status, rtp_age and actions" viewport="900x120"
 */
export interface StreamRowProps
  extends React.HTMLAttributes<HTMLTableRowElement> {
  /** Binding id, e.g. "cam55:color". Falls back to `node:sensor`. */
  binding?: string;
  node: string;
  sensor: string;
  /** Raw stream state, e.g. "online" | "waiting_for_rtp" | "stale". */
  status?: string;
  /** RTP packet age in ms; auto-colored (green <1s, amber <5s, red ≥5s). */
  rtpAgeMs?: number;
  /** Janus mountpoint id. */
  mountpoint?: number | string;
  /** Allocated RTP UDP port. */
  rtpPort?: number | string;
  /** FDIR state for this binding: "enabled" | "disabled" | "suppressed". */
  fdir?: "enabled" | "disabled" | "suppressed";
  /** Short last-error string, shown red. */
  lastError?: string;
  selected?: boolean;
  onOpen?: () => void;
  onRestart?: () => void;
  onStop?: () => void;
  onDiagnose?: () => void;
}

export function StreamRow(props: StreamRowProps): JSX.Element;
