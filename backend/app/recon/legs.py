"""The four joins, each as a ladder of rules from cheap and certain to costly
and uncertain.

The ordering is the whole design. A rule only ever sees the rows the rules above
it could not resolve, so the expensive inference runs on a handful of rows
rather than on the batch, and -- more importantly -- a cheap certain match is
never overridden by an expensive plausible one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from itertools import combinations

from .model import (
    BankRow,
    Exception_,
    InvoiceRow,
    Leg,
    Match,
    PaymentRow,
    SettlementRow,
    Sources,
    Tier,
)
from .normalize import (
    add_business_days,
    canon_name,
    canon_ref,
    classify_operating,
    extract_invoice_refs,
    extract_utr,
    similarity,
)

ROUNDING_TOLERANCE_PAISE = 100
"""A rupee. Beyond this it is not rounding, it is a difference."""

CREDIT_WINDOW_DAYS = 4
"""How long after a payout a credit may legitimately land."""

FUZZY_REF_THRESHOLD = 0.82
"""Below this, a reference match is a coincidence rather than a typo."""


@dataclass
class LegResult:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[Exception_] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    """Classification labels for rows that correctly match nothing."""


# --------------------------------------------------------------------- leg 1


def match_settlements_to_bank(sources: Sources) -> LegResult:
    """Settlement payouts against the credits that actually landed.

    This is the leg that catches money genuinely going missing, so it declines
    rather than guesses: a payout with two equally plausible credits is an
    exception, not a coin toss.
    """
    result = LegResult()
    credits = [t for t in sources.bank_txns if t.is_credit]
    unmatched_settlements = list(sources.settlements)
    claimed: set[str] = set()

    # T1 -- the UTR is right there in the narration.
    by_utr: dict[str, list[BankRow]] = {}
    for txn in credits:
        utr = extract_utr(txn)
        if utr:
            by_utr.setdefault(utr, []).append(txn)

    still_open: list[SettlementRow] = []
    for settlement in unmatched_settlements:
        hits = by_utr.get(settlement.utr.upper(), [])
        if len(hits) == 1:
            txn = hits[0]
            result.matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=txn.txn_id,
                    tier=Tier.T1_EXACT,
                    rule="utr_exact",
                    reason="UTR %s appears in the narration of %s."
                    % (settlement.utr, txn.txn_id),
                    confidence=1.0,
                    amount_paise=settlement.net_paise,
                )
            )
            claimed.add(txn.txn_id)
        elif len(hits) > 1:
            # The same UTR twice is a duplicated bank feed, not two payouts.
            first = hits[0]
            result.matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=first.txn_id,
                    tier=Tier.T1_EXACT,
                    rule="utr_exact_first_of_duplicates",
                    reason="UTR %s appears on %d statement lines; matched the "
                    "earliest and flagged the rest as duplicates."
                    % (settlement.utr, len(hits)),
                    confidence=0.97,
                    amount_paise=settlement.net_paise,
                )
            )
            claimed.add(first.txn_id)
            for dupe in hits[1:]:
                result.exceptions.append(
                    Exception_(
                        entity_type="bank_txn",
                        entity_id=dupe.txn_id,
                        exception_type="DUPLICATE_CREDIT",
                        reason="Same UTR, amount and date as %s. The bank feed "
                        "posted this credit twice." % first.txn_id,
                        amount_paise=dupe.credit_paise,
                        leg=str(Leg.SETTLEMENT_TO_BANK),
                        candidates=[first.txn_id],
                    )
                )
                claimed.add(dupe.txn_id)
        else:
            still_open.append(settlement)

    open_credits = [t for t in credits if t.txn_id not in claimed]

    # T2 -- no usable UTR, so fall back to the amount and a dated window.
    remaining: list[SettlementRow] = []
    for settlement in still_open:
        window = [
            t
            for t in open_credits
            if settlement.settled_at
            <= t.value_date
            <= settlement.settled_at + timedelta(days=CREDIT_WINDOW_DAYS)
            and t.txn_id not in claimed
        ]
        exact = [t for t in window if t.credit_paise == settlement.net_paise]
        near = [
            t
            for t in window
            if abs(t.credit_paise - settlement.net_paise) <= ROUNDING_TOLERANCE_PAISE
            and t not in exact
        ]

        if len(exact) == 1:
            txn = exact[0]
            lag = (txn.value_date - settlement.settled_at).days
            result.matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=txn.txn_id,
                    tier=Tier.T2_DERIVED,
                    rule="net_amount_in_window",
                    reason="Narration carried no UTR. Credit of the exact net "
                    "amount landed %d day(s) after the payout." % lag,
                    confidence=0.95 if lag <= 2 else 0.90,
                    amount_paise=settlement.net_paise,
                )
            )
            claimed.add(txn.txn_id)
        elif not exact and len(near) == 1:
            txn = near[0]
            drift = txn.credit_paise - settlement.net_paise
            result.matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=txn.txn_id,
                    tier=Tier.T2_DERIVED,
                    rule="net_amount_within_rounding",
                    reason="Credit differs from the payout net by %+d paise, "
                    "inside the one-rupee rounding tolerance." % drift,
                    confidence=0.88,
                    amount_paise=settlement.net_paise,
                )
            )
            claimed.add(txn.txn_id)
        else:
            remaining.append(settlement)

    # T3 -- several payouts arriving as one lump credit. This pass needs every
    # credit, claimed ones included: a consolidated credit is claimed by the
    # one UTR it carries while still holding the value of its siblings.
    result.matches.extend(
        _resolve_merged_payouts(remaining, credits, claimed, result)
    )

    # Whatever is left on either side gets named, not buried.
    for settlement in remaining:
        if any(
            m.left_id == settlement.settlement_id for m in result.matches
        ):
            continue
        result.exceptions.append(
            Exception_(
                entity_type="settlement",
                entity_id=settlement.settlement_id,
                exception_type="MISSING_CREDIT",
                reason="Payout of %s was reported on %s but no matching credit "
                "reached the account."
                % (_rupees(settlement.net_paise), settlement.settled_at),
                amount_paise=settlement.net_paise,
                leg=str(Leg.SETTLEMENT_TO_BANK),
            )
        )

    for txn in sources.bank_txns:
        if txn.txn_id in claimed:
            continue
        label = classify_operating(txn.narration)
        if label:
            result.notes[txn.txn_id] = label
    return result


def _resolve_merged_payouts(
    settlements: list[SettlementRow],
    credits: list[BankRow],
    claimed: set[str],
    result: LegResult,
) -> list[Match]:
    """Find credits that are the sum of two or three payouts.

    Bounded on purpose. Subset-sum over an unbounded set will happily find a
    combination for any number given enough rows, and a spurious combination is
    exactly the kind of confident wrong answer this project is built to avoid.
    """
    matches: list[Match] = []
    pool = [s for s in settlements]

    # Pass one: credits that a UTR already claimed but that are too large for
    # the payout they matched. A consolidated credit carries only the first
    # payout's UTR, so the rest of the batch is stranded unless the leftover
    # value is chased explicitly.
    for txn in credits:
        matched_here = [m for m in result.matches if m.right_id == txn.txn_id]
        if not matched_here:
            continue
        residual = txn.credit_paise - sum(m.amount_paise for m in matched_here)
        if residual <= ROUNDING_TOLERANCE_PAISE:
            continue
        nearby = [
            s
            for s in pool
            if abs((txn.value_date - s.settled_at).days) <= CREDIT_WINDOW_DAYS
        ]
        found = _subset_summing_to(nearby, residual)
        if not found:
            continue
        for settlement in found:
            matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=txn.txn_id,
                    tier=Tier.T3_INFERRED,
                    rule="merged_payout_residual",
                    reason="Credit %s exceeds the payout its UTR names by %s, "
                    "which is exactly this payout plus %d other(s)."
                    % (txn.txn_id, _rupees(residual), len(found) - 1),
                    confidence=0.82,
                    amount_paise=settlement.net_paise,
                )
            )
            pool.remove(settlement)

    # Pass two: credits nothing has claimed that equal a sum of payouts.
    for txn in credits:
        if txn.txn_id in claimed:
            continue
        nearby = [
            s
            for s in pool
            if abs((txn.value_date - s.settled_at).days) <= CREDIT_WINDOW_DAYS
        ]
        if len(nearby) < 2:
            continue

        found = _subset_summing_to(nearby, txn.credit_paise, sizes=(2, 3))
        if not found:
            continue

        for settlement in found:
            matches.append(
                Match(
                    leg=Leg.SETTLEMENT_TO_BANK,
                    left_id=settlement.settlement_id,
                    right_id=txn.txn_id,
                    tier=Tier.T3_INFERRED,
                    rule="merged_payout_subset_sum",
                    reason="One credit of %s equals %d payouts summed, this "
                    "one included."
                    % (_rupees(txn.credit_paise), len(found)),
                    confidence=0.80,
                    amount_paise=settlement.net_paise,
                )
            )
            pool.remove(settlement)
        claimed.add(txn.txn_id)

    resolved = {m.left_id for m in matches}
    for settlement in list(settlements):
        if settlement.settlement_id in resolved:
            settlements.remove(settlement)
    return matches


def _subset_summing_to(
    pool: list[SettlementRow], target: int, sizes: tuple[int, ...] = (1, 2, 3)
) -> tuple[SettlementRow, ...] | None:
    """Smallest combination of payouts whose nets hit ``target``.

    Bounded to three members and returned only when the combination is unique.
    Subset-sum over an unbounded pool will find *a* combination for almost any
    number, and an ambiguous one is worse than no answer: it is a confident
    wrong answer, which is the failure mode this whole project exists to avoid.
    """
    for size in sizes:
        if size > len(pool):
            break
        hits = [
            combo
            for combo in combinations(pool, size)
            if abs(sum(s.net_paise for s in combo) - target)
            <= ROUNDING_TOLERANCE_PAISE
        ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None  # ambiguous at this size; do not guess
    return None


# --------------------------------------------------------------------- leg 2


def match_payments_to_settlements(sources: Sources) -> LegResult:
    """Payments against the payout that carried them.

    The gateway usually hands you this link. When it does not, the settlement
    date is derivable from the capture date, and the assignment is then *proved*
    by checking that each payout's assigned payments sum to its gross. That
    proof is the point -- without it, a date rule is just a plausible story.
    """
    result = LegResult()
    settlements = sources.settlement_by_id()
    if not settlements:
        return result

    last_settled = max(s.settled_at for s in settlements.values())
    by_date: dict[object, list[SettlementRow]] = {}
    for settlement in settlements.values():
        by_date.setdefault(settlement.settled_at, []).append(settlement)

    assigned: dict[str, list[PaymentRow]] = {sid: [] for sid in settlements}

    for payment in sources.payments:
        if payment.settlement_id and payment.settlement_id in settlements:
            result.matches.append(
                Match(
                    leg=Leg.PAYMENT_TO_SETTLEMENT,
                    left_id=payment.payment_id,
                    right_id=payment.settlement_id,
                    tier=Tier.T1_EXACT,
                    rule="settlement_id_present",
                    reason="Gateway export carries the settlement id directly.",
                    confidence=1.0,
                    amount_paise=payment.amount_paise,
                )
            )
            assigned[payment.settlement_id].append(payment)
            continue

        expected = add_business_days(payment.captured_at.date(), 2)
        if expected > last_settled:
            result.exceptions.append(
                Exception_(
                    entity_type="payment",
                    entity_id=payment.payment_id,
                    exception_type="PENDING_SETTLEMENT",
                    reason="Captured on %s, so it settles on %s, after the end "
                    "of this statement. Not a break."
                    % (payment.captured_at.date(), expected),
                    amount_paise=payment.amount_paise,
                    leg=str(Leg.PAYMENT_TO_SETTLEMENT),
                )
            )
            continue

        candidates = by_date.get(expected, [])
        if len(candidates) == 1:
            settlement = candidates[0]
            result.matches.append(
                Match(
                    leg=Leg.PAYMENT_TO_SETTLEMENT,
                    left_id=payment.payment_id,
                    right_id=settlement.settlement_id,
                    tier=Tier.T2_DERIVED,
                    rule="t_plus_two_settlement_date",
                    reason="Export omitted the settlement id. Captured %s, so "
                    "T+2 lands on %s, which has exactly one payout."
                    % (payment.captured_at.date(), expected),
                    confidence=0.93,
                    amount_paise=payment.amount_paise,
                )
            )
            assigned[settlement.settlement_id].append(payment)
        else:
            result.exceptions.append(
                Exception_(
                    entity_type="payment",
                    entity_id=payment.payment_id,
                    exception_type="AMBIGUOUS",
                    reason="No settlement id on the export and %d payouts share "
                    "the expected date %s." % (len(candidates), expected),
                    amount_paise=payment.amount_paise,
                    leg=str(Leg.PAYMENT_TO_SETTLEMENT),
                    candidates=[s.settlement_id for s in candidates],
                )
            )

    # The proof step: every payout's assigned payments must sum to its gross.
    for settlement_id, payments in assigned.items():
        settlement = settlements[settlement_id]
        total = sum(p.amount_paise for p in payments)
        if total != settlement.gross_paise:
            result.exceptions.append(
                Exception_(
                    entity_type="settlement",
                    entity_id=settlement_id,
                    exception_type="FEE_MISMATCH",
                    reason="Payments assigned to this payout sum to %s but the "
                    "report says gross %s, a gap of %s."
                    % (
                        _rupees(total),
                        _rupees(settlement.gross_paise),
                        _rupees(settlement.gross_paise - total),
                    ),
                    amount_paise=abs(settlement.gross_paise - total),
                    leg=str(Leg.PAYMENT_TO_SETTLEMENT),
                )
            )
    return result


# --------------------------------------------------------------------- leg 3


def match_payments_to_invoices(sources: Sources) -> LegResult:
    """Gateway payments against the invoices they were meant to clear."""
    result = LegResult()
    invoices = sources.invoices
    by_canon: dict[str, list[InvoiceRow]] = {}
    for invoice in invoices:
        by_canon.setdefault(canon_ref(invoice.invoice_no), []).append(invoice)

    unresolved: list[PaymentRow] = []

    for payment in sources.payments:
        if not payment.invoice_ref:
            unresolved.append(payment)
            continue

        exact = [i for i in invoices if i.invoice_no == payment.invoice_ref]
        if len(exact) == 1:
            result.matches.append(
                _leg3_match(
                    payment, exact[0], Tier.T1_EXACT, "invoice_ref_exact",
                    "Payment quotes invoice %s verbatim." % exact[0].invoice_no,
                    1.0,
                )
            )
            continue

        canon = canon_ref(payment.invoice_ref)
        near = by_canon.get(canon, [])
        if len(near) == 1:
            result.matches.append(
                _leg3_match(
                    payment, near[0], Tier.T2_DERIVED, "invoice_ref_canonical",
                    "Reference %r normalises to %s."
                    % (payment.invoice_ref, near[0].invoice_no),
                    0.94,
                )
            )
            continue

        best, score = _best_fuzzy(canon, by_canon)
        if best is not None and score >= FUZZY_REF_THRESHOLD:
            result.matches.append(
                _leg3_match(
                    payment, best, Tier.T3_INFERRED, "invoice_ref_fuzzy",
                    "Reference %r is %.0f%% similar to %s and the amounts agree."
                    % (payment.invoice_ref, score * 100, best.invoice_no),
                    0.70 + 0.25 * (score - FUZZY_REF_THRESHOLD) / (1 - FUZZY_REF_THRESHOLD),
                )
                if best.amount_paise == payment.amount_paise
                else _leg3_match(
                    payment, best, Tier.T3_INFERRED, "invoice_ref_fuzzy_weak",
                    "Reference %r is %.0f%% similar to %s but the amounts differ."
                    % (payment.invoice_ref, score * 100, best.invoice_no),
                    0.62,
                )
            )
            continue

        unresolved.append(payment)

    result.exceptions.extend(_leg3_resolve_unreferenced(sources, unresolved, result))
    return result


def _leg3_match(
    payment: PaymentRow,
    invoice: InvoiceRow,
    tier: Tier,
    rule: str,
    reason: str,
    confidence: float,
) -> Match:
    return Match(
        leg=Leg.PAYMENT_TO_INVOICE,
        left_id=payment.payment_id,
        right_id=invoice.invoice_no,
        tier=tier,
        rule=rule,
        reason=reason,
        confidence=round(confidence, 3),
        amount_paise=payment.amount_paise,
    )


def _best_fuzzy(
    canon: str, by_canon: dict[str, list[InvoiceRow]]
) -> tuple[InvoiceRow | None, float]:
    best: InvoiceRow | None = None
    best_score = 0.0
    for key, rows in by_canon.items():
        if len(rows) != 1:
            continue
        score = similarity(canon, key)
        if score > best_score:
            best, best_score = rows[0], score
    return best, best_score


def _leg3_resolve_unreferenced(
    sources: Sources, payments: list[PaymentRow], result: LegResult
) -> list[Exception_]:
    """Payments with no usable reference: infer from customer, amount and date.

    Two shapes are worth chasing. A payment for the exact amount of one of that
    customer's open invoices, and a pair of payments that together clear one.
    Anything else is left for a human rather than forced.
    """
    exceptions: list[Exception_] = []
    matched_invoices = {m.right_id for m in result.matches}

    open_invoices = [
        i for i in sources.invoices if i.invoice_no not in matched_invoices
    ]
    by_customer: dict[str, list[InvoiceRow]] = {}
    for invoice in open_invoices:
        by_customer.setdefault(invoice.customer_id, []).append(invoice)

    # The customer master is the only legitimate bridge from the email on a
    # payment to the customer id on an invoice.
    customers_by_email = sources.customers_by_email()

    def invoices_for(email: str) -> list[InvoiceRow]:
        pool: list[InvoiceRow] = []
        for customer in customers_by_email.get(email.lower(), []):
            pool.extend(by_customer.get(customer.customer_id, []))
        return pool

    used: set[str] = set()
    leftovers: list[PaymentRow] = []

    for payment in payments:
        pool = [
            i
            for i in invoices_for(payment.customer_email)
            if i.invoice_no not in used
            and i.issue_date <= payment.captured_at.date() <= i.issue_date + timedelta(days=60)
        ]
        exact = [i for i in pool if i.amount_paise == payment.amount_paise]
        if len(exact) == 1:
            result.matches.append(
                _leg3_match(
                    payment, exact[0], Tier.T3_INFERRED, "customer_amount_date",
                    "No reference on the payment, but this customer has exactly "
                    "one open invoice for %s inside the window."
                    % _rupees(payment.amount_paise),
                    0.78,
                )
            )
            used.add(exact[0].invoice_no)
            continue
        leftovers.append(payment)

    # Split payments: two rows from one customer that together clear an invoice.
    by_email: dict[str, list[PaymentRow]] = {}
    for payment in leftovers:
        by_email.setdefault(payment.customer_email.lower(), []).append(payment)

    still_open: list[PaymentRow] = []
    for email, group in by_email.items():
        pool = [i for i in invoices_for(email) if i.invoice_no not in used]
        consumed: set[str] = set()
        for left, right in combinations(group, 2):
            if left.payment_id in consumed or right.payment_id in consumed:
                continue
            total = left.amount_paise + right.amount_paise
            hits = [i for i in pool if i.amount_paise == total and i.invoice_no not in used]
            if len(hits) != 1:
                continue
            invoice = hits[0]
            for part in (left, right):
                result.matches.append(
                    _leg3_match(
                        part, invoice, Tier.T3_INFERRED, "split_payment_pair",
                        "Two payments from this customer sum to %s, the full "
                        "value of %s." % (_rupees(total), invoice.invoice_no),
                        0.74,
                    )
                )
            used.add(invoice.invoice_no)
            consumed.update({left.payment_id, right.payment_id})
        still_open.extend(p for p in group if p.payment_id not in consumed)

    for payment in still_open:
        exceptions.append(
            Exception_(
                entity_type="payment",
                entity_id=payment.payment_id,
                exception_type="UNBILLED_RECEIPT",
                reason="Captured %s on %s with no invoice reference and no open "
                "invoice that fits. Likely a payment-link sale the ERP never saw."
                % (_rupees(payment.amount_paise), payment.captured_at.date()),
                amount_paise=payment.amount_paise,
                leg=str(Leg.PAYMENT_TO_INVOICE),
            )
        )
    return exceptions


# --------------------------------------------------------------------- leg 4


def match_bank_to_invoices(sources: Sources, claimed: set[str]) -> LegResult:
    """Credits that never touched the gateway, against the invoices they clear.

    These are the receipts a merchant is most likely to lose track of, because
    neither the gateway nor the ERP has any record of them.
    """
    result = LegResult()
    by_canon: dict[str, InvoiceRow] = {}
    for invoice in sources.invoices:
        by_canon.setdefault(canon_ref(invoice.invoice_no), invoice)
    by_name: dict[str, list[InvoiceRow]] = {}
    for invoice in sources.invoices:
        by_name.setdefault(canon_name(invoice.customer_name), []).append(invoice)

    for txn in sources.bank_txns:
        if not txn.is_credit or txn.txn_id in claimed:
            continue
        if classify_operating(txn.narration):
            continue

        refs = extract_invoice_refs(txn.narration)
        hit = next((by_canon[r] for r in refs if r in by_canon), None)
        if hit is not None:
            result.matches.append(
                Match(
                    leg=Leg.BANK_TO_INVOICE,
                    left_id=txn.txn_id,
                    right_id=hit.invoice_no,
                    tier=Tier.T1_EXACT if hit.amount_paise == txn.credit_paise
                    else Tier.T2_DERIVED,
                    rule="narration_invoice_ref",
                    reason="Narration quotes %s and the credit is %s."
                    % (hit.invoice_no, _rupees(txn.credit_paise)),
                    confidence=0.97 if hit.amount_paise == txn.credit_paise else 0.80,
                    amount_paise=txn.credit_paise,
                )
            )
            continue

        # No readable reference: fall back to the payer's name plus the amount.
        payer = _payer_from_narration(txn.narration, by_name)
        pool = [
            i
            for i in by_name.get(payer, [])
            if i.amount_paise == txn.credit_paise
        ]
        if len(pool) == 1:
            result.matches.append(
                Match(
                    leg=Leg.BANK_TO_INVOICE,
                    left_id=txn.txn_id,
                    right_id=pool[0].invoice_no,
                    tier=Tier.T3_INFERRED,
                    rule="payer_name_and_amount",
                    reason="Narration carries no invoice number, but the payer "
                    "name matches and %s is the exact value of %s."
                    % (_rupees(txn.credit_paise), pool[0].invoice_no),
                    confidence=0.72,
                    amount_paise=txn.credit_paise,
                )
            )
            continue

        result.exceptions.append(
            Exception_(
                entity_type="bank_txn",
                entity_id=txn.txn_id,
                exception_type="DIRECT_TRANSFER"
                if payer
                else "UNBILLED_RECEIPT",
                reason="Credit of %s on %s does not correspond to any payout or "
                "invoice. Narration: %s"
                % (_rupees(txn.credit_paise), txn.value_date, txn.narration[:80]),
                amount_paise=txn.credit_paise,
                leg=str(Leg.BANK_TO_INVOICE),
                candidates=[i.invoice_no for i in pool][:5],
            )
        )
    return result


def _payer_from_narration(narration: str, by_name: dict[str, list]) -> str:
    canon = canon_name(narration)
    for name in by_name:
        if name and name in canon:
            return name
    return ""


# ------------------------------------------------------------------- helpers


def _rupees(paise: int) -> str:
    """Format in the Indian convention, because the audience reads lakhs."""
    negative = paise < 0
    whole, part = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return "%sRs %s.%02d" % ("-" if negative else "", digits, part)
