"""Load the three exports and pull structure out of free text.

Bank narration is the messiest field in Indian finance ops: every bank formats
it differently, most truncate it, and the useful part -- a UTR or an invoice
number -- is buried in the middle of a delimiter salad. Everything downstream
gets easier once that field is mined properly, so it is mined here rather than
in the middle of the matching rules.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from .model import (
    BankRow,
    CustomerRow,
    InvoiceRow,
    PaymentRow,
    SettlementRow,
    Sources,
)

# A UTR is 12-22 alphanumerics, usually preceded by the literal "UTR" but not
# always -- some banks emit it bare in the reference column.
UTR_LABELLED = re.compile(r"UTR([A-Z0-9]{10,22})", re.IGNORECASE)
UTR_BARE = re.compile(r"\b([A-Z]{4}[A-Z0-9]{8,18})\b")

# Invoice references survive being mangled surprisingly well, because the digits
# stay put. These patterns catch the common manglings: spaces or nothing in
# place of dashes, a lowercased copy, and the letter O typed for a zero.
INVOICE_REF = re.compile(r"\bINV[\s\-/]?(\d{4}|[O\d]{4})[\s\-/]?(\d{1,6})\b", re.IGNORECASE)

# Narration shapes that are ordinary business traffic, not receivables. A
# merchant edits this list; it is deliberately data, not logic.
OPERATING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^INT\.PD", "bank_interest"),
    (r"BANK CHARGES", "bank_charges"),
    (r"^SALARY", "payroll"),
    (r"^GST PMT", "tax_payment"),
    (r"-DR-.*-PO\d+", "vendor_payout"),
    (r"^ACH-DR", "vendor_autopay"),
    (r"CHARGEBACK", "chargeback"),
)
_OPERATING = tuple((re.compile(p, re.IGNORECASE), label) for p, label in OPERATING_PATTERNS)


def canon_ref(text: str) -> str:
    """Collapse a reference to the form that survives typing errors.

    ``INV-2026-0142``, ``INV 2026 142``, ``inv20260142`` and ``INV-2O26-0142``
    all reduce to the same string, which turns most of the typo class into an
    exact match instead of a fuzzy one.
    """
    if not text:
        return ""
    upper = text.upper().replace("O", "0")
    body = re.sub(r"[^A-Z0-9]", "", upper)
    # Searched rather than anchored, so trailing junk like "/PYMT" does not
    # defeat an otherwise perfectly readable reference.
    match = re.search(r"INV(\d{4})(\d{1,6})", body)
    if match:
        year, serial = match.groups()
        return "INV%s%s" % (year, serial.lstrip("0").rjust(4, "0"))
    return body


def canon_name(text: str) -> str:
    """Strip the corporate furniture so two spellings of a customer align."""
    upper = re.sub(r"[^A-Z0-9 ]", " ", text.upper())
    for suffix in (
        "PRIVATE LIMITED", "PRIVATE LTD", "PVT LTD", "LIMITED", "LTD",
        "LLP", "INC",
    ):
        upper = upper.replace(suffix, " ")
    return " ".join(upper.split())


def similarity(a: str, b: str) -> float:
    """Ratio in [0, 1]. stdlib only, so throughput numbers stay honest."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def extract_utr(row: BankRow) -> str:
    """Best-effort UTR for a statement line, from the ref column or narration."""
    if row.ref_no and len(row.ref_no) >= 10:
        return row.ref_no.upper()
    labelled = UTR_LABELLED.search(row.narration)
    if labelled:
        return labelled.group(1).upper()
    bare = UTR_BARE.search(row.narration.replace("-", " "))
    return bare.group(1).upper() if bare else ""


def extract_invoice_refs(text: str) -> list[str]:
    """Every invoice-shaped token in a narration, canonicalised."""
    out: list[str] = []
    for year, serial in INVOICE_REF.findall(text):
        out.append(canon_ref("INV" + year + serial))
    return out


def classify_operating(narration: str) -> str:
    """Label ordinary business traffic so it never enters the exception queue.

    Interest credits and vendor payouts are not reconciliation failures. Letting
    them pile up as exceptions is the fastest way to make a queue nobody reads.
    """
    for pattern, label in _OPERATING:
        if pattern.search(narration):
            return label
    return ""


def add_business_days(start: date, n: int) -> date:
    out = start
    while n > 0:
        out += timedelta(days=1)
        if out.weekday() < 5:
            n -= 1
    return out


# ------------------------------------------------------------------- loading


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_sources(directory: Path) -> Sources:
    """Read a generated batch directory into typed rows."""
    directory = Path(directory)
    sources = Sources()

    for r in _rows(directory / "erp_customers.csv"):
        sources.customers.append(
            CustomerRow(
                customer_id=r["customer_id"],
                name=r["name"],
                email=r["email"],
                ifsc=r["ifsc"],
            )
        )

    for r in _rows(directory / "erp_invoices.csv"):
        sources.invoices.append(
            InvoiceRow(
                invoice_no=r["invoice_no"],
                customer_id=r["customer_id"],
                customer_name=r["customer_name"],
                issue_date=date.fromisoformat(r["issue_date"]),
                due_date=date.fromisoformat(r["due_date"]),
                amount_paise=int(r["amount_paise"]),
                currency=r["currency"],
                status=r["status"],
                po_ref=r["po_ref"],
            )
        )

    for r in _rows(directory / "pg_payments.csv"):
        sources.payments.append(
            PaymentRow(
                payment_id=r["payment_id"],
                order_id=r["order_id"],
                invoice_ref=r["invoice_ref"],
                customer_email=r["customer_email"],
                method=r["method"],
                amount_paise=int(r["amount_paise"]),
                fee_paise=int(r["fee_paise"]),
                tax_paise=int(r["tax_paise"]),
                status=r["status"],
                captured_at=datetime.fromisoformat(r["captured_at"]),
                settlement_id=r["settlement_id"],
            )
        )

    for r in _rows(directory / "pg_settlements.csv"):
        sources.settlements.append(
            SettlementRow(
                settlement_id=r["settlement_id"],
                utr=r["utr"],
                settled_at=date.fromisoformat(r["settled_at"]),
                gross_paise=int(r["gross_paise"]),
                fee_paise=int(r["fee_paise"]),
                tax_paise=int(r["tax_paise"]),
                adjustment_paise=int(r["adjustment_paise"]),
                net_paise=int(r["net_paise"]),
                payment_count=int(r["payment_count"]),
            )
        )

    for r in _rows(directory / "bank_statement.csv"):
        sources.bank_txns.append(
            BankRow(
                txn_id=r["txn_id"],
                value_date=date.fromisoformat(r["value_date"]),
                narration=r["narration"],
                ref_no=r["ref_no"],
                debit_paise=int(r["debit_paise"]),
                credit_paise=int(r["credit_paise"]),
                balance_paise=int(r["balance_paise"]),
            )
        )

    return sources
