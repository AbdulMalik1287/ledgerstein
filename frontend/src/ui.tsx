/** Shared pieces. Soft cards, pastel marks, generous spacing. */

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
      className={`overflow-hidden rounded-card border border-line bg-card soft-shadow ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
          <div>
            {title && (
              <h2 className="text-[15px] font-semibold tracking-tight text-ink">
                {title}
              </h2>
            )}
            {subtitle && <p className="mt-0.5 text-[13px] text-mute">{subtitle}</p>}
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
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-medium tracking-wide whitespace-nowrap ${
        tone || "bg-card-2 text-ink-2"
      }`}
    >
      {children}
    </span>
  );
}

/**
 * A headline figure. The pastel mark is decorative, but its colour follows the
 * figure's meaning so the row still reads at a glance.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "text-ink",
  mark = "bg-card-2",
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: string;
  mark?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-card border border-line bg-card px-4 py-4 soft-shadow">
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 grid h-8 w-8 flex-none place-items-center rounded-[10px] text-[15px] ${mark}`}
          aria-hidden="true"
        >
          {icon}
        </span>
        <div className="min-w-0">
          <div className="label text-mute">{label}</div>
          <div className={`num mt-1.5 text-[26px] leading-none font-semibold ${tone}`}>
            {value}
          </div>
          {hint && <div className="mt-2 text-[12.5px] text-mute">{hint}</div>}
        </div>
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-12 text-center text-[13.5px] text-mute">{children}</div>
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
      className={`label sticky top-0 z-10 border-b border-line bg-card-2 px-3.5 py-2.5 text-left whitespace-nowrap text-mute ${className}`}
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
      className={`border-b border-line-soft px-3.5 py-3 align-top text-[13.5px] ${className}`}
    >
      {children}
    </td>
  );
}

/** A proportion bar. Rounded and pastel-tracked to match the rest. */
export function Meter({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="h-2 w-16 overflow-hidden rounded-full bg-card-3">
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="num text-[12.5px] text-ink-2">
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

/** A filter button. Used identically across every screen. */
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
      className={`rounded-full border px-3 py-1.5 text-[12px] whitespace-nowrap transition ${
        active
          ? "border-brand bg-brand-bg font-medium text-brand-ink"
          : "border-line bg-card text-ink-2 hover:border-line hover:bg-card-2"
      }`}
    >
      {children}
    </button>
  );
}
