import * as React from "react";

/**
 * Confirmation modal for Class C (service-impacting) and Class D (destructive)
 * actions. Class D requires typing an exact phrase and lays out remove/keep/
 * rollback.
 *
 * @startingPoint section="Overlay" subtitle="Impact + typed-phrase confirm dialog" viewport="700x520"
 */
export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Destructive (Class D) — red treatment + typed-phrase gate. */
  destructive?: boolean;
  message?: string;
  /** Class-C impact bullets. */
  impact?: string[];
  /** Class-D: what will be removed (red). */
  willRemove?: string[];
  /** Class-D: what will stay (green). */
  willKeep?: string[];
  /** Class-D: rollback path text. */
  rollback?: string;
  /** Exact phrase the operator must type (e.g. the node id) to unlock confirm. */
  confirmPhrase?: string;
  confirmLabel?: string;
  onConfirm?: () => void;
  onClose?: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps): JSX.Element | null;
