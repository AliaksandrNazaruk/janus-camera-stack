import * as React from "react";

export type ActionVariant =
  | "primary"
  | "default"
  | "ghost"
  | "warning"
  | "danger"
  | "danger-solid";
export type ActionSize = "xs" | "sm" | "md" | "lg";

/**
 * The console's button primitive — one control for every action class.
 * Variant carries the action's risk: default/ghost for safe & reversible
 * (A/B), warning for service-impacting (C), danger for destructive (D).
 *
 * @startingPoint section="Core" subtitle="Button primitive covering all four action classes" viewport="700x150"
 */
export interface ActionButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ActionVariant;
  size?: ActionSize;
  /** Lucide icon name for the leading glyph (needs Lucide on the page). */
  icon?: string;
  /** Lucide icon name for a trailing glyph. */
  iconRight?: string;
  /** Show a spinner and block clicks while an operation runs. */
  busy?: boolean;
  /** Full-width. */
  block?: boolean;
}

export function ActionButton(props: ActionButtonProps): JSX.Element;
