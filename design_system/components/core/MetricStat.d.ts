import * as React from "react";
import { StatusFamily } from "./StatusBadge";

/**
 * A single Command-Center counter — big mono numeral, uppercase label, status
 * accent bar and optional hint line.
 *
 * @startingPoint section="Core" subtitle="Command-Center metric counter with status accent" viewport="700x150"
 */
export interface MetricStatProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Uppercase label, e.g. "Streams Live". */
  label: string;
  /** Primary numeral (string or number). */
  value: React.ReactNode;
  /** Optional denominator rendered as "/total". */
  total?: React.ReactNode;
  /** Status family for the accent bar + numeral color. Default "idle". */
  family?: StatusFamily;
  /** Secondary line under the numeral. */
  hint?: string;
  /** Lucide icon name shown top-right. */
  icon?: string;
}

export function MetricStat(props: MetricStatProps): JSX.Element;
