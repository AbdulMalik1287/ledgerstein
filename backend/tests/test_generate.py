"""Invariants the synthetic batch must hold.

If these fail, every metric downstream is measuring the generator's bugs rather
than the matcher's ability, so they run first.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.gen.generate import Gen, generate, write_batch
from app.gen.scenarios import GST_RATE


@pytest.fixture(scope="module")
def batch():
    return generate(
        seed=7,
        invoices=240,
        customers=18,
        start=date(2026, 6, 1),
        days=45,
        issue_days=30,
        name="test",
    )


def test_seed_is_deterministic():
    kwargs = dict(
        invoices=60,
        customers=8,
        start=date(2026, 6, 1),
        days=45,
        issue_days=30,
        name="t",
    )
    a = generate(seed=99, **kwargs)
    b = generate(seed=99, **kwargs)
    assert [p.payment_id for p in a.payments] == [p.payment_id for p in b.payments]
    assert a.truth() == b.truth()


def test_ids_are_unique(batch):
    for rows, key in (
        (batch.invoices, "invoice_no"),
        (batch.payments, "payment_id"),
        (batch.settlements, "settlement_id"),
        (batch.bank_txns, "txn_id"),
    ):
        ids = [getattr(r, key) for r in rows]
        assert len(set(ids)) == len(ids), key


def test_settlement_arithmetic_closes(batch):
    """net = gross - fee - tax - adjustment, to the paisa, on every payout."""
    for s in batch.settlements:
        assert s.net_paise == (
            s.gross_paise - s.fee_paise - s.tax_paise - s.adjustment_paise
        )


def test_fee_and_gst_are_consistent(batch):
    for p in batch.payments:
        assert p.tax_paise == int(round(p.fee_paise * GST_RATE))
        assert 0 <= p.fee_paise < p.amount_paise


def test_every_settlement_is_either_credited_or_flagged(batch):
    """A payout must be traceable to a credit or be on the missing list.

    Nothing is allowed to just disappear -- that is the failure mode the whole
    project exists to prevent.
    """
    accounted = set(batch.leg1_settlement_to_bank) | set(
        batch.settlements_without_credit
    )
    assert accounted == {s.settlement_id for s in batch.settlements}


def test_every_payment_is_either_settled_or_pending(batch):
    accounted = set(batch.leg2_payment_to_settlement) | set(batch.pending_payment_ids)
    assert accounted == {p.payment_id for p in batch.payments}


def test_every_payment_is_either_billed_or_unbilled(batch):
    accounted = set(batch.leg3_payment_to_invoice) | set(batch.unbilled_payment_ids)
    assert accounted == {p.payment_id for p in batch.payments}


def test_blanked_settlement_links_are_still_known(batch):
    """The export hides some links; the ground truth must still hold them."""
    blanked = [p for p in batch.payments if not p.settlement_id]
    recoverable = [
        p for p in blanked if p.payment_id in batch.leg2_payment_to_settlement
    ]
    assert recoverable, "no MISSING_SETTLEMENT_LINK rows were seeded"


def test_balance_column_is_a_running_total(batch):
    previous = None
    for row in batch.bank_txns:
        if previous is not None:
            assert row.balance_paise == (
                previous + row.credit_paise - row.debit_paise
            )
        previous = row.balance_paise


def test_statement_is_ordered_by_value_date(batch):
    dates = [t.value_date for t in batch.bank_txns]
    assert dates == sorted(dates)


def test_noise_rows_link_to_nothing(batch):
    """Precision depends on these staying unmatched, so they must be labelled."""
    linked = (
        set(batch.leg1_settlement_to_bank.values())
        | set(batch.leg4_bank_to_invoice)
        | set(batch.chargeback_txn_to_payment)
    )
    assert not linked.intersection(batch.noise_txn_ids)
    assert not linked.intersection(batch.spurious_txn_ids)


def test_merged_payouts_map_many_settlements_to_one_credit(batch):
    seen: dict[str, int] = {}
    for txn_id in batch.leg1_settlement_to_bank.values():
        seen[txn_id] = seen.get(txn_id, 0) + 1
    assert any(count > 1 for count in seen.values()), "no MERGED_PAYOUT was seeded"


def test_direct_transfers_have_no_gateway_row(batch):
    """A NEFT that bypassed the gateway must not appear as a payment."""
    direct_invoices = set(batch.leg4_bank_to_invoice.values())
    billed = set(batch.leg3_payment_to_invoice.values())
    assert not direct_invoices.intersection(billed)


def test_write_batch_emits_every_artefact(batch, tmp_path):
    manifest = write_batch(batch, tmp_path)
    for name in (
        "erp_invoices.csv",
        "pg_payments.csv",
        "pg_settlements.csv",
        "bank_statement.csv",
        "truth.json",
        "manifest.json",
    ):
        assert (tmp_path / name).exists(), name
    assert manifest["counts"]["invoices"] == len(batch.invoices)


def test_business_day_helper_skips_weekends():
    friday = date(2026, 6, 5)
    assert friday.weekday() == 4
    assert Gen._add_business_days(friday, 2) == date(2026, 6, 9)


def test_no_settlement_is_netted_to_zero(batch):
    """A nil payout has no bank line, so it can never be reconciled.

    Clawbacks larger than the payout they land on must be deferred, not
    clamped, or the batch grows a settlement that is unmatchable by
    construction and every recall figure is quietly understated.
    """
    for s in batch.settlements:
        assert s.net_paise > 0, s.settlement_id


def test_every_bank_row_moves_money(batch):
    for t in batch.bank_txns:
        assert (t.credit_paise > 0) != (t.debit_paise > 0), t.txn_id
