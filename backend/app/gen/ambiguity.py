"""Cases where the right answer is not derivable from the data alone.

Every defect in ``scenarios`` is hard but solvable: enough arithmetic and text
normalisation and a rule will get there. That makes for a flattering benchmark
and a dishonest one, because the rows that actually consume a finance team's
week are the ones where two answers are equally consistent with the evidence.

These injectors create that situation on purpose. The ground truth still knows
which answer is correct, but nothing in the exports distinguishes them, so a
matcher that scores full marks here is not clever -- it is guessing, and the
scorecard should catch it doing so.

The intended behaviour on these rows is to decline and raise an AMBIGUOUS
exception carrying both candidates. That is what the adjudicator tier and,
failing that, a human, are for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .records import Batch, Defect


@dataclass
class AmbiguityReport:
    """What was injected, so the docs and the scorecard can name it."""

    twin_invoices: int = 0
    crossed_references: int = 0
    shaved_credits: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "twin_invoices": self.twin_invoices,
            "crossed_references": self.crossed_references,
            "shaved_credits": self.shaved_credits,
        }


def inject(gen, rate: float = 1.0) -> AmbiguityReport:
    """Apply every ambiguity injector to a built batch.

    ``rate`` scales how many of each are attempted, so a batch can be generated
    easy (0.0) or adversarial (1.0) from the same seed.
    """
    report = AmbiguityReport()
    if rate <= 0:
        return report
    report.twin_invoices = _twin_invoices(gen, rate)
    report.shaved_credits = _shaved_credits(gen, rate)
    report.crossed_references = _crossed_references(gen, rate)
    gen.batch.ambiguity_report = report.as_dict()
    return report


def _twin_invoices(gen, rate: float) -> int:
    """Give a ref-less payment two equally plausible invoices.

    Take a payment that carries no reference, and clone the invoice it really
    paid into a second invoice for the same customer, same amount, issued a day
    apart. Both are now consistent with the payment. Nothing in the exports
    says which -- the correct output is a declined match with two candidates.
    """
    batch: Batch = gen.batch
    invoice_by_no = {i.invoice_no: i for i in batch.invoices}
    candidates = [
        payment
        for payment in batch.payments
        if not payment.invoice_ref
        and payment.payment_id in batch.leg3_payment_to_invoice
    ]
    gen.rng.shuffle(candidates)
    target = max(1, int(len(candidates) * 0.30 * rate))
    made = 0

    for payment in candidates[:target]:
        real = invoice_by_no.get(batch.leg3_payment_to_invoice[payment.payment_id])
        if real is None:
            continue
        serial = len(batch.invoices) + 1
        twin = type(real)(
            invoice_no="INV-%d-%04d" % (real.issue_date.year, 9000 + serial),
            customer_id=real.customer_id,
            customer_name=real.customer_name,
            issue_date=real.issue_date + timedelta(days=1),
            due_date=real.due_date + timedelta(days=1),
            amount_paise=real.amount_paise,
            currency=real.currency,
            status="open",
            po_ref=real.po_ref,
        )
        batch.invoices.append(twin)
        # The twin is genuinely unpaid, so it belongs on the receivables list.
        batch.unpaid_invoice_nos.append(twin.invoice_no)
        batch.defects.append(
            Defect(
                "AMBIGUOUS_TWIN_INVOICE",
                {
                    "payment_id": payment.payment_id,
                    "invoice_no": real.invoice_no,
                    "twin_invoice_no": twin.invoice_no,
                },
                "Two invoices from one customer for the same amount, one "
                "reference-less payment. Not decidable from the exports.",
            )
        )
        made += 1
    return made


def _shaved_credits(gen, rate: float) -> int:
    """Deduct a correspondent-bank charge from a credit.

    The credit is then neither the net amount nor within rounding tolerance, so
    an amount rule will not reach it. Recoverable in principle by noticing the
    deduction is a round number, which is exactly the kind of judgement a rule
    should not be trusted to make alone.
    """
    batch: Batch = gen.batch
    bank_by_id = {t.txn_id: t for t in batch.bank_txns}
    linked = [
        (settlement_id, txn_id)
        for settlement_id, txn_id in batch.leg1_settlement_to_bank.items()
    ]
    # Only touch credits carrying exactly one payout, so the shave is
    # attributable rather than tangled up with a merged batch.
    counts: dict[str, int] = {}
    for _, txn_id in linked:
        counts[txn_id] = counts.get(txn_id, 0) + 1
    single = [pair for pair in linked if counts[pair[1]] == 1]
    gen.rng.shuffle(single)

    target = max(1, int(len(single) * 0.10 * rate))
    made = 0
    for settlement_id, txn_id in single[:target]:
        txn = bank_by_id[txn_id]
        charge = gen.rng.choice([500, 1000, 1500, 2500]) * 100
        if txn.credit_paise <= charge * 3:
            continue
        txn.credit_paise -= charge
        txn.narration += "-LESS CHGS"
        batch.defects.append(
            Defect(
                "BANK_CHARGE_DEDUCTED",
                {"settlement_id": settlement_id, "txn_id": txn_id},
                "Correspondent bank deducted a flat charge, so the credit is "
                "below the payout net by more than rounding.",
            )
        )
        made += 1
    _rebuild_balances(batch)
    return made


def _crossed_references(gen, rate: float) -> int:
    """Make a payment quote a real invoice belonging to somebody else.

    A transposed digit does not always land on nothing. Sometimes it lands on
    another customer's live invoice, and then an exact-reference rule produces
    a confident, wrong, fully explainable match -- the most expensive kind.

    The only defence is corroboration: the payer on the invoice has to be the
    payer on the payment. This injector exists to make sure that check is
    actually there rather than assumed.
    """
    batch: Batch = gen.batch
    invoice_by_no = {i.invoice_no: i for i in batch.invoices}
    candidates = [
        payment
        for payment in batch.payments
        if payment.invoice_ref
        and payment.payment_id in batch.leg3_payment_to_invoice
    ]
    gen.rng.shuffle(candidates)
    target = max(1, int(len(candidates) * 0.05 * rate))
    made = 0

    for payment in candidates:
        if made >= target:
            break
        real = invoice_by_no.get(batch.leg3_payment_to_invoice[payment.payment_id])
        if real is None:
            continue
        decoys = [
            i
            for i in batch.invoices
            if i.customer_id != real.customer_id
            and i.amount_paise != payment.amount_paise
        ]
        if not decoys:
            continue
        decoy = gen.rng.choice(decoys)
        payment.invoice_ref = decoy.invoice_no
        batch.defects.append(
            Defect(
                "CROSSED_REFERENCE",
                {
                    "payment_id": payment.payment_id,
                    "invoice_no": real.invoice_no,
                    "quoted_instead": decoy.invoice_no,
                },
                "Payment quotes a real invoice raised on a different customer. "
                "Matching on the reference alone would be confidently wrong.",
            )
        )
        made += 1
    return made


def _rebuild_balances(batch: Batch) -> None:
    balance = 42_00_000_00
    for row in batch.bank_txns:
        balance += row.credit_paise - row.debit_paise
        row.balance_paise = balance
