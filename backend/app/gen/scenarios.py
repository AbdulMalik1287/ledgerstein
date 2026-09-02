"""Defect taxonomy for the synthetic three-source dataset.

Every row Kosh generates is either clean or carries exactly one seeded defect.
The defect label is written to ``truth.json`` so the metrics harness can report
recall *per defect class* rather than one averaged number that hides the hard
cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InvoiceFate(StrEnum):
    """What happens to an invoice after it is raised."""

    PAID_CLEAN = "PAID_CLEAN"
    """One captured payment for the exact invoice amount, reference intact."""

    SPLIT_PAYMENT = "SPLIT_PAYMENT"
    """Two part-payments that only reconcile once summed."""

    DIRECT_TRANSFER = "DIRECT_TRANSFER"
    """Customer bypassed the gateway and did a NEFT straight to the bank."""

    TYPO_REF = "TYPO_REF"
    """Payment carries a mangled invoice reference; needs fuzzy matching."""

    MISSING_REF = "MISSING_REF"
    """Payment carries no invoice reference at all; needs inference."""

    REFUNDED = "REFUNDED"
    """Paid, then refunded; the refund nets off a later settlement."""

    OVERDUE_INVOICE = "OVERDUE_INVOICE"
    """Never paid. Past due date. A receivable, not a reconciliation failure."""


class PaymentDefect(StrEnum):
    """What goes wrong on the gateway side of a payment row."""

    CLEAN = "CLEAN"
    MISSING_SETTLEMENT_LINK = "MISSING_SETTLEMENT_LINK"
    """Older PG exports omit ``settlement_id``; leg 2 must be derived."""

    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    """Captured too late in the window to have settled yet. Not an error."""

    UNBILLED = "UNBILLED"
    """A storefront or payment-link sale that never got an ERP invoice."""


class BankDefect(StrEnum):
    """What goes wrong between a settlement and the bank credit for it."""

    CLEAN = "CLEAN"
    TIMING_SKEW = "TIMING_SKEW"
    """Credit lands one or two days after the settlement date."""

    MERGED_PAYOUT = "MERGED_PAYOUT"
    """Several settlements arrive as a single lump credit."""

    DUPLICATE_CREDIT = "DUPLICATE_CREDIT"
    """Bank feed emitted the same credit twice; one is spurious."""

    MISSING_CREDIT = "MISSING_CREDIT"
    """Settlement is on hold; the money never arrived."""

    ROUNDING_DRIFT = "ROUNDING_DRIFT"
    """Credit differs from the settlement net by a few paise."""


class ExceptionType(StrEnum):
    """Typed reasons a row can land in the exception queue.

    These are what a human sees, so they are phrased as diagnoses rather than
    as "unmatched".
    """

    TIMING_SKEW = "TIMING_SKEW"
    FEE_MISMATCH = "FEE_MISMATCH"
    MERGED_PAYOUT = "MERGED_PAYOUT"
    SPLIT_PAYMENT = "SPLIT_PAYMENT"
    REFUND_REVERSAL = "REFUND_REVERSAL"
    DUPLICATE_CREDIT = "DUPLICATE_CREDIT"
    MISSING_CREDIT = "MISSING_CREDIT"
    CHARGEBACK_DEBIT = "CHARGEBACK_DEBIT"
    ROUNDING_DRIFT = "ROUNDING_DRIFT"
    DIRECT_TRANSFER = "DIRECT_TRANSFER"
    ORPHAN_PAYMENT = "ORPHAN_PAYMENT"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    UNBILLED_RECEIPT = "UNBILLED_RECEIPT"
    OVERDUE_INVOICE = "OVERDUE_INVOICE"
    AMBIGUOUS = "AMBIGUOUS"


# Weightings tuned so a batch looks like a real month: mostly clean, with a
# long tail that is where all the finance-team hours actually go.
INVOICE_FATE_WEIGHTS: dict[InvoiceFate, float] = {
    InvoiceFate.PAID_CLEAN: 0.52,
    InvoiceFate.SPLIT_PAYMENT: 0.07,
    InvoiceFate.DIRECT_TRANSFER: 0.08,
    InvoiceFate.TYPO_REF: 0.07,
    InvoiceFate.MISSING_REF: 0.11,
    InvoiceFate.REFUNDED: 0.05,
    InvoiceFate.OVERDUE_INVOICE: 0.10,
}

BANK_DEFECT_WEIGHTS: dict[BankDefect, float] = {
    BankDefect.CLEAN: 0.56,
    BankDefect.TIMING_SKEW: 0.14,
    BankDefect.MERGED_PAYOUT: 0.11,
    BankDefect.DUPLICATE_CREDIT: 0.07,
    BankDefect.MISSING_CREDIT: 0.06,
    BankDefect.ROUNDING_DRIFT: 0.06,
}


@dataclass(frozen=True)
class MethodPricing:
    """Razorpay-shaped pricing. Rates are fractions of the captured amount."""

    method: str
    rate: float
    weight: float


# UPI is zero-MDR in India, which is exactly why a naive amount-equality
# matcher looks deceptively good until it meets a card payment.
METHOD_PRICING: tuple[MethodPricing, ...] = (
    MethodPricing("upi", 0.0000, 0.46),
    MethodPricing("card", 0.0200, 0.24),
    MethodPricing("netbanking", 0.0175, 0.16),
    MethodPricing("wallet", 0.0200, 0.08),
    MethodPricing("nach", 0.0150, 0.06),
)

GST_RATE = 0.18
"""GST charged on the gateway fee, not on the transaction amount."""

SETTLEMENT_LAG_DAYS = 2
"""Razorpay's standard T+2 settlement cycle, in business days."""
