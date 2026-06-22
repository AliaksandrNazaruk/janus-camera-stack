import * as React from "react";
import { StatusFamily } from "../core/StatusBadge";

export interface TimelineEvent {
  id?: string | number;
  /** Mono timestamp, e.g. "14:22". */
  time: string;
  /** Target id shown in mono, e.g. "cam55:color". */
  target?: string;
  /** Human message. */
  message: string;
  /** Result/level used for color when `family` is absent: "ok"/"failed"/… */
  result?: string;
  level?: string;
  /** Force a color family. */
  family?: StatusFamily;
  actor?: string;
  action?: string;
  reason?: string;
}

/**
 * Chronological FDIR / audit / activity feed with a status-colored rail.
 *
 * @startingPoint section="Data" subtitle="FDIR / audit event timeline" viewport="700x300"
 */
export interface EventTimelineProps extends React.HTMLAttributes<HTMLDivElement> {
  events?: TimelineEvent[];
  dense?: boolean;
}

export function EventTimeline(props: EventTimelineProps): JSX.Element;
