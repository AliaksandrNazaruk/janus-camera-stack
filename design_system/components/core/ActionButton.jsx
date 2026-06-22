import React from "react";

/* Variant → visual treatment. The four action classes from the spec map onto
 * these: Class A/B safe & reversible use "default"/"ghost"; Class A primary
 * path uses "primary"; Class C service-impacting uses "warning"; Class D
 * destructive uses "danger". */
const VARIANTS = {
  primary: {
    bg: "var(--action)", color: "var(--action-text)",
    border: "var(--blue-700)", hoverBg: "var(--action-hover)",
  },
  default: {
    bg: "var(--surface-card)", color: "var(--text-body)",
    border: "var(--border-default)", hoverBg: "var(--surface-hover)",
  },
  ghost: {
    bg: "transparent", color: "var(--text-muted)",
    border: "transparent", hoverBg: "var(--surface-hover)",
  },
  warning: {
    bg: "var(--amber-50)", color: "var(--amber-800)",
    border: "var(--status-warn-border)", hoverBg: "var(--amber-100)",
  },
  danger: {
    bg: "var(--red-50)", color: "var(--red-700)",
    border: "var(--status-bad-border)", hoverBg: "var(--red-100)",
  },
  "danger-solid": {
    bg: "var(--red-600)", color: "var(--white)",
    border: "var(--red-700)", hoverBg: "var(--red-700)",
  },
};

const SIZES = {
  xs: { pad: "2px 8px", font: "var(--text-xs)", h: 24, gap: 4, icon: 13 },
  sm: { pad: "4px 10px", font: "var(--text-sm)", h: 28, gap: 5, icon: 14 },
  md: { pad: "6px 14px", font: "var(--text-base)", h: 34, gap: 6, icon: 16 },
  lg: { pad: "9px 18px", font: "var(--text-md)", h: 40, gap: 7, icon: 18 },
};

/**
 * ActionButton — the console's button primitive. One control covers every
 * action class via `variant`; pass a Lucide icon name to `icon` to get the
 * leading glyph (requires the Lucide script on the page).
 */
export function ActionButton({
  children,
  variant = "default",
  size = "md",
  icon,
  iconRight,
  disabled = false,
  busy = false,
  block = false,
  style,
  ...rest
}) {
  const v = VARIANTS[variant] || VARIANTS.default;
  const s = SIZES[size] || SIZES.md;
  const [hover, setHover] = React.useState(false);
  const Icon = (name) =>
    name ? (
      <i
        data-lucide={name}
        style={{ width: s.icon, height: s.icon, flex: "none" }}
      />
    ) : null;
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      onMouseEnter={(e) => { setHover(true); rest.onMouseEnter && rest.onMouseEnter(e); }}
      onMouseLeave={(e) => { setHover(false); rest.onMouseLeave && rest.onMouseLeave(e); }}
      style={{
        display: block ? "flex" : "inline-flex",
        width: block ? "100%" : undefined,
        alignItems: "center",
        justifyContent: "center",
        gap: s.gap,
        minHeight: s.h,
        padding: s.pad,
        borderRadius: "var(--radius-sm)",
        border: `1px solid ${v.border}`,
        background: hover && !disabled ? v.hoverBg : v.bg,
        color: v.color,
        font: `var(--weight-semibold) ${s.font}/1 var(--font-sans)`,
        cursor: disabled ? "not-allowed" : busy ? "wait" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background 120ms ease, border-color 120ms ease",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {busy ? <i data-lucide="loader-circle" style={{ width: s.icon, height: s.icon, animation: "gc-spin 0.8s linear infinite" }} /> : Icon(icon)}
      {children}
      {Icon(iconRight)}
    </button>
  );
}
