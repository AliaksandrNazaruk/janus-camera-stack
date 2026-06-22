import * as React from "react";

export interface OperationStep {
  label: string;
  /** "pending" | "active" | "ok" | "failed". */
  state: "pending" | "active" | "ok" | "failed";
}

/**
 * The right-side drawer every mutation opens — states impact + FDIR + duration
 * before confirm, then streams step progress (request → ack → verify → result).
 *
 * @startingPoint section="Overlay" subtitle="Operation drawer: impact preview + step progress" viewport="900x560"
 */
export interface OperationDrawerProps {
  open: boolean;
  title: string;
  /** Target id in mono, e.g. "cam55:color". */
  target?: string;
  /** Action class A/B/C/D — drives the header tag color and confirm style. */
  impactClass?: "A" | "B" | "C" | "D";
  /** Bullet list of consequences. */
  impact?: string[];
  /** FDIR consequence text, e.g. "stays enabled" / "will be disabled". */
  fdirNote?: string;
  /** Expected duration, e.g. "5–15s". */
  duration?: string;
  /** Step progress; show after confirm. */
  steps?: OperationStep[];
  /** Spinner on the confirm button while the op runs. */
  running?: boolean;
  /** Final result: "ok" | "failed". Switches footer to Done. */
  result?: "ok" | "failed";
  confirmLabel?: string;
  onConfirm?: () => void;
  onClose?: () => void;
}

export function OperationDrawer(props: OperationDrawerProps): JSX.Element | null;
