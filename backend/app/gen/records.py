"""Row shapes for the three synthetic sources.

Money is carried as integer paise everywhere. Floats never touch an amount --
a reconciliation engine that is off by a paisa because of binary rounding is
worse than useless, because the error is invisible until it is large.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime


@dataclass
class Customer:
    customer_id: str
    name: str
    email: str
    ifsc: str


@dataclass
class Invoice:
    """A row as the merchant's ERP exports it."""

    invoice_no: str
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    amount_paise: int
    currency: str
    status: str
    """What the ERP *believes*. Deliberately stale in places -- see the
    DIRECT_TRANSFER and REFUNDED fates."""
    po_ref: str


@dataclass
class Payment:
    """A row as Razorpay's payments export gives it."""

    payment_id: str
    order_id: str
    invoice_ref: str
    customer_email: str
    method: str
    amount_paise: int
    fee_paise: int
    tax_paise: int
    status: str
    captured_at: datetime
    settlement_id: str
    """Blank where the export omits it -- leg 2 then has to be derived."""


@dataclass
class Settlement:
    """A Razorpay payout batch: many payments, one bank credit."""

    settlement_id: str
    utr: str
    settled_at: date
    gross_paise: int
    fee_paise: int
    tax_paise: int
    adjustment_paise: int
    """Refunds and disputes clawed back from this payout.

    Present in real settlement reports, and the reason ``net`` is not simply
    ``gross - fee - tax``. A matcher that forgets this column breaks on every
    settlement that follows a refund.
    """
    net_paise: int
    payment_count: int


@dataclass
class BankTxn:
    """A line from the merchant's current-account statement."""

    txn_id: str
    value_date: date
    narration: str
    ref_no: str
    debit_paise: int
    credit_paise: int
    balance_paise: int


@dataclass
class Defect:
    """One seeded imperfection, recorded so metrics can be reported per class."""

    defect_type: str
    entities: dict[str, str | list[str]]
    note: str


@dataclass
class Batch:
    """Everything one generated month contains, plus its ground truth."""

    name: str
    seed: int
    customers: list[Customer] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    bank_txns: list[BankTxn] = field(default_factory=list)

    # Ground truth. Keys are source ids; values are what they truly link to.
    leg1_settlement_to_bank: dict[str, str] = field(default_factory=dict)
    leg2_payment_to_settlement: dict[str, str] = field(default_factory=dict)
    leg3_payment_to_invoice: dict[str, str] = field(default_factory=dict)
    leg4_bank_to_invoice: dict[str, str] = field(default_factory=dict)
    """Direct NEFT receipts that bypassed the gateway entirely, so they have no
    payment or settlement row to hang off."""

    chargeback_txn_to_payment: dict[str, str] = field(default_factory=dict)
    """Debits the bank raised against a payment that had already settled."""

    # Rows that correctly match nothing. A matcher that pairs these up is
    # losing precision, and precision is the number that costs money.
    noise_txn_ids: list[str] = field(default_factory=list)
    unpaid_invoice_nos: list[str] = field(default_factory=list)
    unbilled_payment_ids: list[str] = field(default_factory=list)
    pending_payment_ids: list[str] = field(default_factory=list)
    settlements_without_credit: list[str] = field(default_factory=list)
    spurious_txn_ids: list[str] = field(default_factory=list)

    defects: list[Defect] = field(default_factory=list)
    ambiguity_report: dict = field(default_factory=dict)

    def truth(self) -> dict:
        return {
            "leg1_settlement_to_bank": self.leg1_settlement_to_bank,
            "leg2_payment_to_settlement": self.leg2_payment_to_settlement,
            "leg3_payment_to_invoice": self.leg3_payment_to_invoice,
            "leg4_bank_to_invoice": self.leg4_bank_to_invoice,
            "chargeback_txn_to_payment": self.chargeback_txn_to_payment,
            "correctly_unmatched": {
                "noise_txn_ids": self.noise_txn_ids,
                "spurious_txn_ids": self.spurious_txn_ids,
                "unpaid_invoice_nos": self.unpaid_invoice_nos,
                "unbilled_payment_ids": self.unbilled_payment_ids,
                "pending_payment_ids": self.pending_payment_ids,
                "settlements_without_credit": self.settlements_without_credit,
            },
            "defects": [asdict(d) for d in self.defects],
        }
