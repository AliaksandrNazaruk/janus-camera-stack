import * as React from "react";

/**
 * Desired vs actual reconcile view for the Fleet page. Three columns; drift
 * rows are highlighted amber.
 *
 * @startingPoint section="Data" subtitle="Fleet desired-vs-actual drift diff" viewport="800x220"
 */
export interface DriftDiffProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Desired-state lines (mono). */
  desired?: string[];
  /** Actual-state lines; prefix a line with "!" to flag it amber. */
  actual?: string[];
  /** Drift lines; empty array renders an "in sync" confirmation. */
  drift?: string[];
}

export function DriftDiff(props: DriftDiffProps): JSX.Element;
