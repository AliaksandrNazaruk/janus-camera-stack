import * as React from "react";

export interface NodeStreamSummary {
  sensor: string;
  status: string;
  mp: number | string;
  port: number | string;
  rtpAge: string;
}

export interface NodeHealth {
  agent?: string;
  camera?: string;
  lastSeen?: string;
  provision?: string;
  maintenance?: "on" | "off";
  hostKey?: "pinned" | "unpinned" | "pending";
  token?: string;
}

/**
 * A physical camera node — local gateway or remote producer, rendered
 * identically. Health grid + per-sensor streams + action row + Danger Zone.
 *
 * @startingPoint section="Data" subtitle="Full node management card with health grid and Danger Zone" viewport="700x420"
 */
export interface NodeCardProps extends React.HTMLAttributes<HTMLDivElement> {
  nodeId: string;
  host: string;
  role?: string;
  model?: string;
  serial?: string;
  /** Raw node state, e.g. "online" | "maintenance" | "offline". */
  status?: string;
  /** Local gateway node → blue accent; remote → slate accent. */
  local?: boolean;
  health?: NodeHealth;
  streams?: NodeStreamSummary[];
  onCheck?: () => void;
  onProvision?: () => void;
  onMaintenance?: () => void;
  onRotate?: () => void;
  onOpenStreams?: () => void;
  onRemove?: () => void;
}

export function NodeCard(props: NodeCardProps): JSX.Element;
