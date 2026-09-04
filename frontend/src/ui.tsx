/**
 * Shared components.
 *
 * One radius system, one transition, one focus ring. Every interactive piece
 * here ships default, hover, active, focus and disabled; half a state machine
 * is what makes a product UI feel subtly wrong.
 */

import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  right,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`overflow-hidden rounded-card border border-line bg-surface lift ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3.5">
          <div>
            {title && (
              <h2 className="text-[15px] leading-tight font-semibold tracking-[-0.011em] text-ink">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-1 text-[12.5px] text-ink-2">{subtitle}</p>
            )}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Tag({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-[3px] text-[10.5px] font-medium tracking-[0.02em] whitespace-nowrap ${
        tone || "bg-sunk text-ink-2"
      }`}
    >
      {children}
    </span>
  );
}

/**
 * A headline figure.
 *
 * No icon, no tinted chip. The number is the content and colour here is
 * semantic: green when the figure is good, amber when it wants attention.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "text-ink",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-4 lift">
      <div className="label text-faint">{label}</div>
      <div
        className={`num mt-2 text-[27px] leading-none font-medium tracking-[-0.02em] ${tone}`}
      >
        {value}
      </div>
      {hint && <div className="mt-2 text-[12px] text-ink-2">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-14 text-center text-[13px] text-ink-2">
      {children}
    </div>
  );
}

export function Th({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`label sticky top-0 z-10 border-b border-line bg-raised px-3.5 py-2.5 text-left whitespace-nowrap text-faint ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <td
      className={`border-b border-line-soft px-3.5 py-3 align-top text-[13px] ${className}`}
    >
      {children}
    </td>
  );
}

/** A proportion bar. No filled track competing with the fill itself. */
export function Meter({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="h-[5px] w-16 overflow-hidden rounded-full bg-sunk">
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="num text-[12px] text-ink-2">
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

/** The one filter control. Identical on every screen. */
export function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-3 py-[5px] text-[12px] whitespace-nowrap active:scale-[0.98] ${
        active
          ? "border-accent bg-accent font-medium text-surface"
          : "border-line bg-surface text-ink-2 hover:border-line-soft hover:bg-raised hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

/** Primary action. Ink, because colour on this screen means state. */
export function Button({
  onClick,
  disabled,
  children,
  className = "",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded-full bg-accent px-5 py-2 text-[12.5px] font-medium text-surface hover:opacity-90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
    >
      {children}
    </button>
  );
}

/**
 * Loading placeholder shaped like the rows it replaces, so the layout does not
 * jump when real data lands.
 */
export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-3 px-4 py-4" aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-4">
          <div className="h-4 w-24 rounded-full bg-sunk" />
          <div className="h-4 w-36 rounded-full bg-sunk/70" />
          <div className="h-4 flex-1 rounded-full bg-sunk/50" />
        </div>
      ))}
    </div>
  );
}
