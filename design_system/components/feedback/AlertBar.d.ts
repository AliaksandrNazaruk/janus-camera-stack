import * as React from "react";

/**
 * The global alert strip pinned under the topbar — highest open severity,
 * count, lead message and optional action.
 *
 * @startingPoint section="Feedback" subtitle="Global severity alert bar" viewport="900x60"
 */
export interface AlertBarProps extends React.HTMLAttributes<HTMLDivElement> {
  severity?: "critical" | "warning" | "info";
  message: React.ReactNode;
  /** Count of open alerts at this severity. */
  count?: number;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss?: () => void;
}

export function AlertBar(props: AlertBarProps): JSX.Element;
