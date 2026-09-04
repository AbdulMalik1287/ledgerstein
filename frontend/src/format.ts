/** Formatting helpers. Money is shown the way the audience reads it. */

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

const inrCompact = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 1,
  notation: "compact",
});

/** Full precision, lakh-and-crore grouped. For anything being reconciled. */
export const rupees = (value: number) => inr.format(value);

/** Compact, for headline tiles where the exact paisa is not the point. */
export const rupeesShort = (value: number) => inrCompact.format(value);

export const percent = (value: number, digits = 1) =>
  `${(value * 100).toFixed(digits)}%`;

export const count = (value: number) => value.toLocaleString("en-IN");

/** "leg3_payment_to_invoice" reads as "3 · payment to invoice". */
export function legLabel(leg: string): string {
  const match = /^leg(\d)_(.*)$/.exec(leg);
  if (!match) return leg.replace(/_/g, " ");
  return `${match[1]} · ${match[2].replace(/_/g, " ")}`;
}

export const tierLabel = (tier: string) =>
  tier.replace("_", " ").toLowerCase().replace(/^t(\d)/, "T$1");

/**
 * Exception types coloured by what they mean, not by severity alone.
 * Money that may be gone is red; things merely waiting are neutral.
 */
export function exceptionTone(type: string): string {
  switch (type) {
    case "MISSING_CREDIT":
    case "CHARGEBACK_DEBIT":
    case "DUPLICATE_CREDIT":
      return "bg-bad-bg text-bad";
    case "AMBIGUOUS":
    case "CROSSED_REFERENCE":
    case "VALUE_VARIANCE":
    case "FEE_MISMATCH":
      return "bg-warn-bg text-warn";
    case "PENDING_SETTLEMENT":
      return "bg-sunk text-ink-2";
    default:
      return "bg-accent-soft text-ink";
  }
}

export function tierTone(tier: string): string {
  switch (tier) {
    case "T1_EXACT":
      return "bg-good-bg text-good";
    case "T2_DERIVED":
      return "bg-accent-soft text-ink";
    case "T3_INFERRED":
      return "bg-warn-bg text-warn";
    default:
      return "bg-bad-bg text-bad";
  }
}
