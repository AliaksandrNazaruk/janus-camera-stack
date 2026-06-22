import * as React from "react";

export interface HealthService {
  name: string;
  /** Raw state, e.g. "healthy" | "synced" | "enabled". */
  status: string;
  /** Optional label override (defaults to de-underscored status). */
  label?: string;
}

/**
 * The Command-Center "System Status" strip — a title plus service health
 * pills. The left accent auto-reflects the worst service state.
 *
 * @startingPoint section="Data" subtitle="System status strip — service health at a glance" viewport="900x90"
 */
export interface HealthCardProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  services?: HealthService[];
}

export function HealthCard(props: HealthCardProps): JSX.Element;
