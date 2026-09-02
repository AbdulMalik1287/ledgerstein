"""What the reconciliation engine produces.

Two things travel with every result and neither is optional: the rule that made
the decision, and a sentence a finance person can read. A match nobody can
explain is not a match, it is a guess with good posture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Leg(StrEnum):
    """The four joins that together close the loop on a rupee."""

    SETTLEMENT_TO_BANK = "leg1_settlement_to_bank"
    PAYMENT_TO_SETTLEMENT = "leg2_payment_to_settlement"
    PAYMENT_TO_INVOICE = "leg3_payment_to_invoice"
    BANK_TO_INVOICE = "leg4_bank_to_invoice"


class Tier(StrEnum):
    """Which stage of the ladder resolved a row.

    Reported per tier because 'we matched 94%' means nothing without knowing
    how much of it a plain key join would have got for free.
    """

    T1_EXACT = "T1_EXACT"
    T2_DERIVED = "T2_DERIVED"
    T3_INFERRED = "T3_INFERRED"
    T4_ADJUDICATED = "T4_ADJUDICATED"


CONFIDENCE_FLOOR: dict[Tier, float] = {
    Tier.T1_EXACT: 1.00,
    Tier.T2_DERIVED: 0.93,
    Tier.T3_INFERRED: 0.70,
    Tier.T4_ADJUDICATED: 0.55,
}


@dataclass
class Match:
    """One resolved link between two source rows."""

    leg: Leg
    left_id: str
    right_id: str
    tier: Tier
    rule: str
    """Stable identifier of the rule that fired, e.g. ``utr_exact``."""
    reason: str
    """Plain English. Shown verbatim in the UI and the audit trail."""
    confidence: float
    amount_paise: int = 0

    def key(self) -> tuple[str, str, str]:
        return (str(self.leg), self.left_id, self.right_id)


@dataclass
class Exception_:
    """A row the engine refused to force into a match.

    Named with a trailing underscore because ``Exception`` is taken and
    shadowing it in a finance codebase is a genuinely bad idea.
    """

    entity_type: str
    entity_id: str
    exception_type: str
    reason: str
    amount_paise: int = 0
    leg: str = ""
    candidates: list[str] = field(default_factory=list)
    """Ids the engine considered but could not choose between."""


@dataclass
class AuditEvent:
    """Append-only. One row per decision, including the decisions to decline."""

    sequence: int
    at: datetime
    actor: str
    """``rule:<name>`` or ``llm:<model>`` or ``human:<user>``."""
    action: str
    """``match``, ``decline``, ``flag``, ``resolve``."""
    leg: str
    subject: str
    detail: str
    confidence: float = 0.0


# ------------------------------------------------------------------- sources


@dataclass
class CustomerRow:
    """The ERP customer master.

    Without it there is no honest way to get from a payment (which carries an
    email) to an invoice (which carries a customer id), and guessing the join
    from a naming convention is not reconciliation, it is luck.
    """

    customer_id: str
    name: str
    email: str
    ifsc: str


@dataclass
class InvoiceRow:
    invoice_no: str
    customer_id: str
    customer_name: str
    issue_date: date
    due_date: date
    amount_paise: int
    currency: str
    status: str
    po_ref: str


@dataclass
class PaymentRow:
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


@dataclass
class SettlementRow:
    settlement_id: str
    utr: str
    settled_at: date
    gross_paise: int
    fee_paise: int
    tax_paise: int
    adjustment_paise: int
    net_paise: int
    payment_count: int


@dataclass
class BankRow:
    txn_id: str
    value_date: date
    narration: str
    ref_no: str
    debit_paise: int
    credit_paise: int
    balance_paise: int

    @property
    def is_credit(self) -> bool:
        return self.credit_paise > 0

    @property
    def amount_paise(self) -> int:
        return self.credit_paise or self.debit_paise


@dataclass
class Sources:
    """The three exports, parsed and indexed."""

    customers: list[CustomerRow] = field(default_factory=list)
    invoices: list[InvoiceRow] = field(default_factory=list)
    payments: list[PaymentRow] = field(default_factory=list)
    settlements: list[SettlementRow] = field(default_factory=list)
    bank_txns: list[BankRow] = field(default_factory=list)

    def row_count(self) -> int:
        return (
            len(self.invoices)
            + len(self.payments)
            + len(self.settlements)
            + len(self.bank_txns)
        )

    def customers_by_email(self) -> dict[str, list[CustomerRow]]:
        """Email to customers. Plural on purpose: group companies share a
        mailbox, so this join is one-to-many and pretending otherwise silently
        attributes payments to the wrong company."""
        index: dict[str, list[CustomerRow]] = {}
        for customer in self.customers:
            index.setdefault(customer.email.lower(), []).append(customer)
        return index

    def invoice_by_no(self) -> dict[str, InvoiceRow]:
        return {i.invoice_no: i for i in self.invoices}

    def payment_by_id(self) -> dict[str, PaymentRow]:
        return {p.payment_id: p for p in self.payments}

    def settlement_by_id(self) -> dict[str, SettlementRow]:
        return {s.settlement_id: s for s in self.settlements}

    def bank_by_id(self) -> dict[str, BankRow]:
        return {t.txn_id: t for t in self.bank_txns}
