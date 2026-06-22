import * as React from "react";

export interface DiagnosticRow {
  key: string;
  value: React.ReactNode;
  /** Optional state string — colors the value + adds a dot. */
  status?: string;
}

/**
 * A titled block of key/value diagnostic rows (Agent / Camera / Services /
 * Data-plane / Control-plane). Values render in mono.
 *
 * @startingPoint section="Data" subtitle="Key/value diagnostics panel" viewport="440x300"
 */
export interface DiagnosticsPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  /** Lucide icon name for the header. */
  icon?: string;
  rows?: DiagnosticRow[];
}

export function DiagnosticsPanel(props: DiagnosticsPanelProps): JSX.Element;
