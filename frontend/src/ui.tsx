/** Small shared pieces. Kept plain so the screens stay readable. */

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
      className={`rounded-lg border border-line bg-ink-800 ${className}`}
    >
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-4 border-b border-line px-4 py-3">
          <div>
            {title && (
              <h2 className="text-sm font-semibold tracking-tight text-bright">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-mute">{subtitle}</p>
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
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium tracking-wide whitespace-nowrap ${
        tone || "border-line bg-ink-600 text-mute"
      }`}
    >
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "text-bright",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-ink-800 px-4 py-3">
      <div className="text-[11px] font-medium tracking-wide text-mute uppercase">
        {label}
      </div>
      <div className={`num mt-1 text-2xl leading-tight font-semibold ${tone}`}>
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-mute">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-mute">{children}</div>
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
      className={`sticky top-0 z-10 border-b border-line bg-ink-700 px-3 py-2 text-left text-[11px] font-semibold tracking-wide text-mute uppercase ${className}`}
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
    <td className={`border-b border-line/60 px-3 py-2 align-top ${className}`}>
      {children}
    </td>
  );
}

/** A horizontal proportion bar. Used for precision and recall in tables. */
export function Meter({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-600">
        <div
          className={`h-full rounded-full ${tone}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="num text-xs">{(value * 100).toFixed(1)}%</span>
    </div>
  );
}
