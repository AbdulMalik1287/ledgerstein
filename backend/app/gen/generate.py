"""Generate a synthetic month of merchant finance data across three sources.

The point of this module is not to make pretty CSVs. It is to make *hard* ones:
every defect seeded here is one a finance team actually chases every month, and
because the generator knows the answer, the matcher can be scored honestly
instead of demoed on a cherry-picked row.

Run it::

    python -m app.gen.generate --seed 7 --invoices 240 --out data/generated/batch_a
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import string
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .ambiguity import inject as inject_ambiguity
from .records import Batch, BankTxn, Customer, Defect, Invoice, Payment, Settlement
from .scenarios import (
    BANK_DEFECT_WEIGHTS,
    GST_RATE,
    INVOICE_FATE_WEIGHTS,
    METHOD_PRICING,
    SETTLEMENT_LAG_DAYS,
    BankDefect,
    InvoiceFate,
)

ALNUM = string.ascii_lowercase + string.digits

COMPANY_HEADS = [
    "Meridian", "Kavach", "Northwind", "Saraswat", "Blueleaf", "Tridev",
    "Alpine", "Chandra", "Ironwood", "Vasant", "Redgrove", "Anantara",
    "Silverline", "Bharat", "Quartz", "Pallava", "Eastgate", "Nirmaan",
    "Halcyon", "Girija", "Copperfield", "Aravalli", "Westbrook", "Sundara",
]
COMPANY_TAILS = [
    "Textiles", "Logistics", "Foods", "Systems", "Traders", "Industries",
    "Polymers", "Engineering", "Agro", "Retail", "Labs", "Interiors",
]
VENDOR_NAMES = [
    "SHREE PACKAGING", "AIRTEL BUSINESS", "TATA POWER DDL", "AWS INDIA",
    "GODOWN RENT LLP", "COURIER EXPRESS PVT", "INDIGO CORPORATE",
]
IFSC_CODES = [
    "HDFC0000123", "ICIC0001234", "SBIN0004567", "UTIB0000456",
    "KKBK0000789", "YESB0000234", "IDFB0040101",
]

# Invoice sizes cluster in bands the way a real B2B ledger does: a wall of
# small recurring invoices, a middle, and a handful of large ones that carry
# most of the value -- and therefore most of the risk of a wrong match.
AMOUNT_BANDS: tuple[tuple[int, int, float], ...] = (
    (1_200_00, 25_000_00, 0.50),
    (25_000_00, 1_20_000_00, 0.34),
    (1_20_000_00, 4_50_000_00, 0.13),
    (4_50_000_00, 18_00_000_00, 0.03),
)


class Gen:
    """Seeded generator. Same seed in, identical batch out."""

    def __init__(
        self, seed: int, name: str, start: date, days: int, issue_days: int
    ) -> None:
        self.rng = random.Random(seed)
        self.batch = Batch(name=name, seed=seed)
        self.start = start
        self.days = days
        # Invoices stop being raised well before the statement window closes.
        # Without that gap almost every payment is still inside its T+2 cycle
        # at the cut-off, which makes the pending pile look like a defect rate
        # rather than the tail it actually is.
        self.issue_days = min(issue_days, days)
        self.end = start + timedelta(days=days)

    # ---------------------------------------------------------------- helpers

    def _rid(self, prefix: str, n: int = 14) -> str:
        return prefix + "".join(self.rng.choices(ALNUM, k=n))

    def _pick(self, weights: dict):
        keys = list(weights)
        return self.rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]

    def _amount(self) -> int:
        lo, hi, _ = self.rng.choices(
            AMOUNT_BANDS, weights=[b[2] for b in AMOUNT_BANDS], k=1
        )[0]
        # Round to whole rupees; invoices are not priced in paise.
        return self.rng.randint(lo // 100, hi // 100) * 100

    def _day(self) -> date:
        return self.start + timedelta(days=self.rng.randrange(self.issue_days))

    @staticmethod
    def _add_business_days(d: date, n: int) -> date:
        out = d
        while n > 0:
            out += timedelta(days=1)
            if out.weekday() < 5:
                n -= 1
        return out

    # -------------------------------------------------------------- customers

    def customers(self, n: int) -> list[Customer]:
        seen: set[str] = set()
        out: list[Customer] = []
        while len(out) < n:
            name = (
                self.rng.choice(COMPANY_HEADS)
                + " "
                + self.rng.choice(COMPANY_TAILS)
                + " Pvt Ltd"
            )
            if name in seen:
                continue
            seen.add(name)
            words = name.split()
            slug = (words[0] + words[1]).lower()
            # One customer in eight routes through a shared group mailbox, so
            # an email does not uniquely identify a company. That is how real
            # accounts-payable teams work and the matcher has to cope.
            email = (
                "ap@" + words[0].lower() + "group.co.in"
                if self.rng.random() < 0.125
                else "accounts@" + slug + ".co.in"
            )
            out.append(
                Customer(
                    customer_id="CUST%04d" % (len(out) + 1),
                    name=name,
                    email=email,
                    ifsc=self.rng.choice(IFSC_CODES),
                )
            )
        self.batch.customers = out
        return out

    # --------------------------------------------------------------- invoices

    def invoices(self, n: int) -> list[tuple[Invoice, InvoiceFate]]:
        pairs: list[tuple[Invoice, InvoiceFate]] = []
        for i in range(n):
            cust = self.rng.choice(self.batch.customers)
            issued = self._day()
            fate = self._pick(INVOICE_FATE_WEIGHTS)
            inv = Invoice(
                invoice_no="INV-%d-%04d" % (issued.year, i + 1),
                customer_id=cust.customer_id,
                customer_name=cust.name,
                issue_date=issued,
                due_date=issued + timedelta(days=self.rng.choice([15, 30, 30, 45])),
                amount_paise=self._amount(),
                currency="INR",
                status=self._erp_status(fate),
                po_ref="PO/%02d/%d" % (issued.year % 100, self.rng.randrange(1000, 9999)),
            )
            pairs.append((inv, fate))
        self.batch.invoices = [p[0] for p in pairs]
        return pairs

    def _erp_status(self, fate: InvoiceFate) -> str:
        """What the ERP thinks, which is not always what happened.

        Stale statuses are the whole reason a three-way match beats trusting
        any single system, so they are seeded on purpose.
        """
        if fate in (InvoiceFate.PAID_CLEAN, InvoiceFate.TYPO_REF):
            return "paid"
        if fate is InvoiceFate.MISSING_REF:
            return self.rng.choice(["paid", "open"])
        if fate is InvoiceFate.SPLIT_PAYMENT:
            return "partial"
        if fate is InvoiceFate.DIRECT_TRANSFER:
            return "open"  # customer paid the bank directly; the ERP never knew
        if fate is InvoiceFate.REFUNDED:
            return "paid"  # refund not yet posted back to the ledger
        if fate is InvoiceFate.OVERDUE_INVOICE:
            return "overdue"
        return "open"

    # --------------------------------------------------------------- payments

    def payments(
        self, invoice_pairs: list[tuple[Invoice, InvoiceFate]]
    ) -> list[Payment]:
        out: list[Payment] = []
        self.direct_transfers: list[Invoice] = []

        for inv, fate in invoice_pairs:
            if fate is InvoiceFate.OVERDUE_INVOICE:
                self.batch.unpaid_invoice_nos.append(inv.invoice_no)
                self.batch.defects.append(
                    Defect(
                        "OVERDUE_INVOICE",
                        {"invoice_no": inv.invoice_no},
                        "Raised and never collected. A receivable, not a break.",
                    )
                )
            elif fate is InvoiceFate.DIRECT_TRANSFER:
                # Settled entirely on the bank side; no gateway row exists.
                self.direct_transfers.append(inv)
                self.batch.defects.append(
                    Defect(
                        "DIRECT_TRANSFER",
                        {"invoice_no": inv.invoice_no},
                        "Paid by NEFT straight to the bank, bypassing the gateway.",
                    )
                )
            elif fate is InvoiceFate.SPLIT_PAYMENT:
                first = inv.amount_paise // 2 // 100 * 100
                pair = [
                    self._payment_for(inv, first),
                    self._payment_for(inv, inv.amount_paise - first),
                ]
                out.extend(pair)
                self.batch.defects.append(
                    Defect(
                        "SPLIT_PAYMENT",
                        {
                            "invoice_no": inv.invoice_no,
                            "payment_ids": [p.payment_id for p in pair],
                        },
                        "One invoice cleared by two part-payments.",
                    )
                )
            elif fate is InvoiceFate.REFUNDED:
                pay = self._payment_for(inv, inv.amount_paise)
                pay.status = "refunded"
                out.append(pay)
                self.batch.defects.append(
                    Defect(
                        "REFUND_REVERSAL",
                        {"invoice_no": inv.invoice_no, "payment_id": pay.payment_id},
                        "Captured then refunded. Nets off a later settlement.",
                    )
                )
            elif fate is InvoiceFate.TYPO_REF:
                pay = self._payment_for(inv, inv.amount_paise)
                pay.invoice_ref = self._mangle(inv.invoice_no)
                out.append(pay)
                self.batch.defects.append(
                    Defect(
                        "TYPO_REF",
                        {
                            "invoice_no": inv.invoice_no,
                            "payment_id": pay.payment_id,
                            "seen_as": pay.invoice_ref,
                        },
                        "Customer mistyped the invoice reference.",
                    )
                )
            elif fate is InvoiceFate.MISSING_REF:
                pay = self._payment_for(inv, inv.amount_paise)
                pay.invoice_ref = ""
                out.append(pay)
                self.batch.defects.append(
                    Defect(
                        "MISSING_REF",
                        {"invoice_no": inv.invoice_no, "payment_id": pay.payment_id},
                        "No reference on the payment. Must be inferred.",
                    )
                )
            else:
                out.append(self._payment_for(inv, inv.amount_paise))

        out.extend(self._unbilled_payments())
        self.rng.shuffle(out)
        self.batch.payments = out
        return out

    def _payment_for(self, inv: Invoice, amount: int) -> Payment:
        pricing = self.rng.choices(
            METHOD_PRICING, weights=[m.weight for m in METHOD_PRICING], k=1
        )[0]
        fee = int(round(amount * pricing.rate))
        captured_day = min(
            inv.issue_date + timedelta(days=self.rng.randrange(0, 26)),
            self.end - timedelta(days=1),
        )
        captured = datetime.combine(
            captured_day,
            time(
                self.rng.randrange(6, 22),
                self.rng.randrange(60),
                self.rng.randrange(60),
            ),
        )
        email = next(
            c.email
            for c in self.batch.customers
            if c.customer_id == inv.customer_id
        )
        pay = Payment(
            payment_id=self._rid("pay_"),
            order_id=self._rid("order_"),
            invoice_ref=inv.invoice_no,
            customer_email=email,
            method=pricing.method,
            amount_paise=amount,
            fee_paise=fee,
            tax_paise=int(round(fee * GST_RATE)),
            status="captured",
            captured_at=captured,
            settlement_id="",  # filled in by settle()
        )
        self.batch.leg3_payment_to_invoice[pay.payment_id] = inv.invoice_no
        return pay

    def _unbilled_payments(self) -> list[Payment]:
        """Payment-link and storefront sales that never got an ERP invoice."""
        out: list[Payment] = []
        for _ in range(max(2, len(self.batch.invoices) // 30)):
            pricing = self.rng.choices(
                METHOD_PRICING, weights=[m.weight for m in METHOD_PRICING], k=1
            )[0]
            amount = self.rng.randint(1_500, 60_000) * 100
            fee = int(round(amount * pricing.rate))
            cust = self.rng.choice(self.batch.customers)
            day = self._day()
            pay = Payment(
                payment_id=self._rid("pay_"),
                order_id=self._rid("order_"),
                invoice_ref="",
                customer_email=cust.email,
                method=pricing.method,
                amount_paise=amount,
                fee_paise=fee,
                tax_paise=int(round(fee * GST_RATE)),
                status="captured",
                captured_at=datetime.combine(
                    day, time(self.rng.randrange(9, 21), self.rng.randrange(60))
                ),
                settlement_id="",
            )
            out.append(pay)
            self.batch.unbilled_payment_ids.append(pay.payment_id)
            self.batch.defects.append(
                Defect(
                    "UNBILLED_RECEIPT",
                    {"payment_id": pay.payment_id},
                    "Payment-link sale with no matching ERP invoice.",
                )
            )
        return out

    @staticmethod
    def _mangle_variants(ref: str) -> list[str]:
        parts = ref.split("-")
        short = parts[-1].lstrip("0") or "0"
        joined = parts[0] + short if len(parts) == 3 else ref
        return [
            ref.replace("-", " "),
            ref.replace("0", "O"),
            ref.lower(),
            joined,
            ref + "/PYMT",
            ref.replace("-", ""),
        ]

    def _mangle(self, ref: str) -> str:
        return self.rng.choice(self._mangle_variants(ref))

    # ------------------------------------------------------------ settlements

    def settle(self, payments: list[Payment]) -> list[Settlement]:
        """Batch payments into T+2 payouts, the way Razorpay actually pays out.

        Two things here are deliberately awkward for a matcher. Payments
        captured near the cut-off have not settled yet and must be recognised
        as pending rather than missing. And some rows come back from the export
        with no ``settlement_id`` at all, so leg 2 has to be reconstructed from
        amounts and dates.
        """
        buckets: dict[date, list[Payment]] = {}
        for pay in payments:
            settle_date = self._add_business_days(
                pay.captured_at.date(), SETTLEMENT_LAG_DAYS
            )
            if settle_date >= self.end:
                self.batch.pending_payment_ids.append(pay.payment_id)
                self.batch.defects.append(
                    Defect(
                        "PENDING_SETTLEMENT",
                        {"payment_id": pay.payment_id},
                        "Captured inside the T+2 window; not a break.",
                    )
                )
                continue
            buckets.setdefault(settle_date, []).append(pay)

        # Each refund claws back from the first payout on or after it, so a
        # refund lands on exactly one settlement and never double-counts.
        settle_dates = sorted(buckets)
        clawback: dict[date, int] = {}
        for pay in payments:
            if pay.status != "refunded":
                continue
            refunded_on = pay.captured_at.date() + timedelta(
                days=self.rng.randrange(4, 14)
            )
            target = next((d for d in settle_dates if d >= refunded_on), None)
            if target is not None:
                clawback[target] = clawback.get(target, 0) + pay.amount_paise

        settlements: list[Settlement] = []
        carry = 0
        for settle_date in settle_dates:
            group = buckets[settle_date]
            gross = sum(p.amount_paise for p in group)
            fee = sum(p.fee_paise for p in group)
            tax = sum(p.tax_paise for p in group)

            # A clawback bigger than the payout it lands on is deferred to the
            # next one rather than zeroing this one out. Gateways behave this
            # way because a bank does not post a nil-value credit, and a payout
            # netted to zero would leave a settlement row with no statement
            # line to reconcile against -- an artefact, not a defect.
            available = gross - fee - tax
            wanted = clawback.get(settle_date, 0) + carry
            if wanted >= available:
                adjustment, carry = 0, wanted
            else:
                adjustment, carry = wanted, 0
            settlement = Settlement(
                settlement_id=self._rid("setl_"),
                utr=self._utr(settle_date),
                settled_at=settle_date,
                gross_paise=gross,
                fee_paise=fee,
                tax_paise=tax,
                adjustment_paise=adjustment,
                net_paise=gross - fee - tax - adjustment,
                payment_count=len(group),
            )
            settlements.append(settlement)

            for pay in group:
                self.batch.leg2_payment_to_settlement[pay.payment_id] = (
                    settlement.settlement_id
                )
                # Most exports carry the link. Some do not, and those are the
                # rows that make leg 2 an actual problem rather than a join.
                if self.rng.random() < 0.14:
                    pay.settlement_id = ""
                    self.batch.defects.append(
                        Defect(
                            "MISSING_SETTLEMENT_LINK",
                            {
                                "payment_id": pay.payment_id,
                                "settlement_id": settlement.settlement_id,
                            },
                            "Export omitted the settlement id; must be derived.",
                        )
                    )
                else:
                    pay.settlement_id = settlement.settlement_id

            if adjustment:
                self.batch.defects.append(
                    Defect(
                        "REFUND_ADJUSTMENT",
                        {"settlement_id": settlement.settlement_id},
                        "Payout reduced by a refund, so net is below gross less fees.",
                    )
                )

        self.batch.settlements = settlements
        return settlements

    def _utr(self, when: date) -> str:
        return "HDFCN%s%06d" % (when.strftime("%y%j"), self.rng.randrange(1, 999999))

    # ---------------------------------------------------------------- banking

    def bank(self, settlements: list[Settlement]) -> list[BankTxn]:
        """Build the current-account statement, defects and all.

        Bank rows are created first and numbered last, because a statement is
        ordered by value date and a plausible one has to look that way.
        """
        drafts: list[tuple[BankTxn, str, str]] = []  # row, kind, linked id

        def credit(when: date, narration: str, ref: str, amount: int) -> BankTxn:
            return BankTxn(
                txn_id="",
                value_date=when,
                narration=narration,
                ref_no=ref,
                debit_paise=0,
                credit_paise=amount,
                balance_paise=0,
            )

        def debit(when: date, narration: str, ref: str, amount: int) -> BankTxn:
            return BankTxn(
                txn_id="",
                value_date=when,
                narration=narration,
                ref_no=ref,
                debit_paise=amount,
                credit_paise=0,
                balance_paise=0,
            )

        idx = 0
        while idx < len(settlements):
            settlement = settlements[idx]
            defect = self._pick(BANK_DEFECT_WEIGHTS)

            if defect is BankDefect.MERGED_PAYOUT and idx + 1 < len(settlements):
                span = min(self.rng.choice([2, 2, 3]), len(settlements) - idx)
                group = settlements[idx : idx + span]
                row = credit(
                    max(s.settled_at for s in group),
                    "NEFT-CR-HDFC0000123-RAZORPAY SOFTWARE PVT LTD-UTR"
                    + group[0].utr
                    + "-CONSOLIDATED",
                    group[0].utr,
                    sum(s.net_paise for s in group),
                )
                for s in group:
                    drafts.append((row, "settlement", s.settlement_id))
                self.batch.defects.append(
                    Defect(
                        "MERGED_PAYOUT",
                        {"settlement_ids": [s.settlement_id for s in group]},
                        "%d payouts arrived as one lump credit." % span,
                    )
                )
                idx += span
                continue

            if defect is BankDefect.MISSING_CREDIT:
                self.batch.settlements_without_credit.append(settlement.settlement_id)
                self.batch.defects.append(
                    Defect(
                        "MISSING_CREDIT",
                        {"settlement_id": settlement.settlement_id},
                        "Payout on hold. The money never reached the account.",
                    )
                )
                idx += 1
                continue

            value_date = settlement.settled_at
            amount = settlement.net_paise
            if defect is BankDefect.TIMING_SKEW:
                value_date = settlement.settled_at + timedelta(
                    days=self.rng.choice([1, 1, 2])
                )
                self.batch.defects.append(
                    Defect(
                        "TIMING_SKEW",
                        {"settlement_id": settlement.settlement_id},
                        "Credit landed after the settlement date.",
                    )
                )
            elif defect is BankDefect.ROUNDING_DRIFT:
                amount += self.rng.choice([-1, 1]) * self.rng.randrange(1, 99)
                self.batch.defects.append(
                    Defect(
                        "ROUNDING_DRIFT",
                        {"settlement_id": settlement.settlement_id},
                        "Credit differs from the payout net by a few paise.",
                    )
                )

            # Some bank feeds truncate the narration and drop the UTR, which
            # forces a fallback to amount and date.
            utr_visible = self.rng.random() < 0.85
            narration = (
                "NEFT-CR-HDFC0000123-RAZORPAY SOFTWARE PVT LTD-UTR"
                + settlement.utr
                + "-SETTLEMENT"
                if utr_visible
                else "NEFT-CR-HDFC0000123-RAZORPAY SOFTWARE PVT LTD-PAYOUT"
            )
            row = credit(
                value_date, narration, settlement.utr if utr_visible else "", amount
            )
            drafts.append((row, "settlement", settlement.settlement_id))

            if defect is BankDefect.DUPLICATE_CREDIT:
                clone = credit(value_date, narration, row.ref_no, amount)
                drafts.append((clone, "spurious", ""))
                self.batch.defects.append(
                    Defect(
                        "DUPLICATE_CREDIT",
                        {"settlement_id": settlement.settlement_id},
                        "Bank feed emitted the same credit twice.",
                    )
                )

            idx += 1

        drafts.extend(self._direct_transfer_rows(credit))
        drafts.extend(self._chargeback_rows(debit))
        drafts.extend(self._noise_rows(credit, debit))

        return self._finalise(drafts)

    def _direct_transfer_rows(self, credit) -> list[tuple[BankTxn, str, str]]:
        out: list[tuple[BankTxn, str, str]] = []
        for inv in getattr(self, "direct_transfers", []):
            cust = next(
                c for c in self.batch.customers if c.customer_id == inv.customer_id
            )
            ref_text = (
                inv.invoice_no
                if self.rng.random() < 0.7
                else self._mangle(inv.invoice_no)
            )
            when = inv.issue_date + timedelta(days=self.rng.randrange(3, 40))
            narration = (
                "NEFT-CR-"
                + cust.ifsc
                + "-"
                + cust.name.upper()
                + "-"
                + ref_text
            )
            out.append(
                (
                    credit(when, narration, "N%09d" % self.rng.randrange(1, 10**9),
                           inv.amount_paise),
                    "direct",
                    inv.invoice_no,
                )
            )
        return out

    def _chargeback_rows(self, debit) -> list[tuple[BankTxn, str, str]]:
        """Debits raised against payments that had already settled."""
        out: list[tuple[BankTxn, str, str]] = []
        settled = [
            p
            for p in self.batch.payments
            if p.payment_id in self.batch.leg2_payment_to_settlement
        ]
        if not settled:
            return out
        for pay in self.rng.sample(settled, k=min(3, len(settled))):
            when = pay.captured_at.date() + timedelta(days=self.rng.randrange(20, 50))
            out.append(
                (
                    debit(
                        when,
                        "NEFT-DR-RAZORPAY SOFTWARE PVT LTD-CHARGEBACK-" + pay.payment_id,
                        pay.payment_id,
                        pay.amount_paise,
                    ),
                    "chargeback",
                    pay.payment_id,
                )
            )
            self.batch.defects.append(
                Defect(
                    "CHARGEBACK_DEBIT",
                    {"payment_id": pay.payment_id},
                    "Bank clawed back a settled payment after a dispute.",
                )
            )
        return out

    def _noise_rows(self, credit, debit) -> list[tuple[BankTxn, str, str]]:
        """Operating traffic that must match nothing.

        Precision is what these rows measure. A matcher that pairs an interest
        credit to a settlement looks great on recall and is quietly wrong.
        """
        out: list[tuple[BankTxn, str, str]] = []
        for _ in range(max(12, int(len(self.batch.settlements) * 1.6))):
            kind = self.rng.choice(
                ["vendor", "vendor", "utility", "salary", "charges", "interest", "gst"]
            )
            when = self._day()
            if kind == "vendor":
                row = debit(
                    when,
                    "RTGS-DR-"
                    + self.rng.choice(IFSC_CODES)
                    + "-"
                    + self.rng.choice(VENDOR_NAMES)
                    + "-PO"
                    + str(self.rng.randrange(10000, 99999)),
                    "R%09d" % self.rng.randrange(1, 10**9),
                    self.rng.randrange(20_000, 8_00_000) * 100,
                )
            elif kind == "utility":
                row = debit(
                    when,
                    "ACH-DR-" + self.rng.choice(VENDOR_NAMES) + "-AUTOPAY",
                    "",
                    self.rng.randrange(2_000, 90_000) * 100,
                )
            elif kind == "salary":
                row = debit(
                    when,
                    "SALARY-" + when.strftime("%b%Y").upper() + "-BULK",
                    "",
                    self.rng.randrange(4_00_000, 22_00_000) * 100,
                )
            elif kind == "charges":
                row = debit(
                    when, "BANK CHARGES-NEFT-" + when.strftime("%b%y").upper(), "",
                    self.rng.randrange(50, 900) * 100,
                )
            elif kind == "gst":
                row = debit(
                    when,
                    "GST PMT-CHALLAN-" + str(self.rng.randrange(10**10, 10**11)),
                    "",
                    self.rng.randrange(30_000, 5_00_000) * 100,
                )
            else:
                row = credit(
                    when,
                    "INT.PD:" + when.strftime("%d%m%Y"),
                    "",
                    self.rng.randrange(400, 26_000) * 100,
                )
            out.append((row, "noise", ""))
        return out

    def _finalise(self, drafts: list[tuple[BankTxn, str, str]]) -> list[BankTxn]:
        """Order the statement by value date, number it, and run the balance."""
        rows: list[BankTxn] = []
        seen: set[int] = set()
        for row, _, _ in drafts:
            if id(row) not in seen:
                seen.add(id(row))
                rows.append(row)
        rows.sort(key=lambda r: (r.value_date, r.narration))

        balance = 42_00_000_00  # a plausible opening balance
        for i, row in enumerate(rows, start=1):
            row.txn_id = "TXN%06d" % i
            balance += row.credit_paise - row.debit_paise
            row.balance_paise = balance

        for row, kind, target in drafts:
            if kind == "settlement":
                self.batch.leg1_settlement_to_bank[target] = row.txn_id
            elif kind == "direct":
                self.batch.leg4_bank_to_invoice[row.txn_id] = target
            elif kind == "chargeback":
                self.batch.chargeback_txn_to_payment[row.txn_id] = target
            elif kind == "spurious":
                self.batch.spurious_txn_ids.append(row.txn_id)
            elif kind == "noise":
                self.batch.noise_txn_ids.append(row.txn_id)

        self.batch.bank_txns = rows
        return rows

    # ------------------------------------------------------------------ build

    def build(
        self, invoice_count: int, customer_count: int, ambiguity: float = 1.0
    ) -> Batch:
        self.customers(customer_count)
        pairs = self.invoices(invoice_count)
        payments = self.payments(pairs)
        settlements = self.settle(payments)
        self.bank(settlements)
        self.ambiguity = inject_ambiguity(self, rate=ambiguity)
        return self.batch


# ---------------------------------------------------------------------- output


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def write_batch(batch: Batch, out_dir: Path) -> dict:
    """Write the three sources, the ground truth, and a manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(
        out_dir / "erp_customers.csv",
        ["customer_id", "name", "email", "ifsc"],
        [[c.customer_id, c.name, c.email, c.ifsc] for c in batch.customers],
    )

    _write_csv(
        out_dir / "erp_invoices.csv",
        [
            "invoice_no", "customer_id", "customer_name", "issue_date",
            "due_date", "amount_paise", "currency", "status", "po_ref",
        ],
        [
            [
                i.invoice_no, i.customer_id, i.customer_name,
                i.issue_date.isoformat(), i.due_date.isoformat(),
                i.amount_paise, i.currency, i.status, i.po_ref,
            ]
            for i in batch.invoices
        ],
    )

    _write_csv(
        out_dir / "pg_payments.csv",
        [
            "payment_id", "order_id", "invoice_ref", "customer_email", "method",
            "amount_paise", "fee_paise", "tax_paise", "status", "captured_at",
            "settlement_id",
        ],
        [
            [
                p.payment_id, p.order_id, p.invoice_ref, p.customer_email,
                p.method, p.amount_paise, p.fee_paise, p.tax_paise, p.status,
                p.captured_at.isoformat(sep=" "), p.settlement_id,
            ]
            for p in batch.payments
        ],
    )

    _write_csv(
        out_dir / "pg_settlements.csv",
        [
            "settlement_id", "utr", "settled_at", "gross_paise", "fee_paise",
            "tax_paise", "adjustment_paise", "net_paise", "payment_count",
        ],
        [
            [
                s.settlement_id, s.utr, s.settled_at.isoformat(), s.gross_paise,
                s.fee_paise, s.tax_paise, s.adjustment_paise, s.net_paise,
                s.payment_count,
            ]
            for s in batch.settlements
        ],
    )

    _write_csv(
        out_dir / "bank_statement.csv",
        [
            "txn_id", "value_date", "narration", "ref_no", "debit_paise",
            "credit_paise", "balance_paise",
        ],
        [
            [
                t.txn_id, t.value_date.isoformat(), t.narration, t.ref_no,
                t.debit_paise, t.credit_paise, t.balance_paise,
            ]
            for t in batch.bank_txns
        ],
    )

    (out_dir / "truth.json").write_text(
        json.dumps(batch.truth(), indent=2), encoding="utf-8"
    )

    manifest = {
        "batch": batch.name,
        "seed": batch.seed,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "customers": len(batch.customers),
            "invoices": len(batch.invoices),
            "payments": len(batch.payments),
            "settlements": len(batch.settlements),
            "bank_txns": len(batch.bank_txns),
            "defects": len(batch.defects),
        },
        "defect_mix": _defect_mix(batch),
        "ambiguity": getattr(batch, "ambiguity_report", {}),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def _defect_mix(batch: Batch) -> dict[str, int]:
    mix: dict[str, int] = {}
    for defect in batch.defects:
        mix[defect.defect_type] = mix.get(defect.defect_type, 0) + 1
    return dict(sorted(mix.items(), key=lambda kv: -kv[1]))


def generate(
    seed: int,
    invoices: int,
    customers: int,
    start: date,
    days: int,
    issue_days: int,
    name: str,
    ambiguity: float = 1.0,
) -> Batch:
    gen = Gen(seed=seed, name=name, start=start, days=days, issue_days=issue_days)
    gen.build(
        invoice_count=invoices, customer_count=customers, ambiguity=ambiguity
    )
    return gen.batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--invoices", type=int, default=240)
    parser.add_argument("--customers", type=int, default=18)
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument(
        "--days", type=int, default=45, help="statement window length"
    )
    parser.add_argument(
        "--issue-days", type=int, default=30, help="days over which invoices are raised"
    )
    parser.add_argument("--out", default="data/generated/batch_a")
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--ambiguity",
        type=float,
        default=1.0,
        help="0 for a solvable batch, 1 for one with irreducible ambiguity",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    batch = generate(
        seed=args.seed,
        invoices=args.invoices,
        customers=args.customers,
        start=date.fromisoformat(args.start),
        days=args.days,
        issue_days=args.issue_days,
        name=args.name or out_dir.name,
        ambiguity=args.ambiguity,
    )
    manifest = write_batch(batch, out_dir)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
