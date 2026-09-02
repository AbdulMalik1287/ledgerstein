"""The adjudicator's safety properties, tested without touching the network.

The whole argument for letting a model near a ledger is that its answer is
checked before it is believed. These tests are that argument: a hallucinated
identifier must be rejected rather than matched, a hedged answer must fall
below the confidence floor, and a run without credentials must degrade to
leaving rows in the queue rather than to a crash or a guess.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.recon.adjudicator import Adjudicator, Verdict
from app.recon.model import Exception_, InvoiceRow, Leg, PaymentRow, Sources


class StubResponse:
    def __init__(self, verdict: Verdict) -> None:
        self.parsed_output = verdict


class StubMessages:
    def __init__(self, verdicts: list[Verdict | Exception]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        nxt = self._verdicts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return StubResponse(nxt)


class StubClient:
    def __init__(self, *verdicts) -> None:
        self.messages = StubMessages(list(verdicts))


def _sources() -> Sources:
    sources = Sources()
    sources.payments.append(
        PaymentRow(
            payment_id="pay_1",
            order_id="order_1",
            invoice_ref="",
            customer_email="accounts@meridiantextiles.co.in",
            method="upi",
            amount_paise=250000,
            fee_paise=0,
            tax_paise=0,
            status="captured",
            captured_at=datetime(2026, 6, 20, 11, 0),
            settlement_id="setl_1",
        )
    )
    for serial, issued in ((1, date(2026, 6, 2)), (2, date(2026, 6, 3))):
        sources.invoices.append(
            InvoiceRow(
                invoice_no="INV-2026-%04d" % serial,
                customer_id="CUST0001",
                customer_name="Meridian Textiles Pvt Ltd",
                issue_date=issued,
                due_date=issued,
                amount_paise=250000,
                currency="INR",
                status="open",
                po_ref="PO/26/1234",
            )
        )
    return sources


def _exception() -> Exception_:
    return Exception_(
        entity_type="payment",
        entity_id="pay_1",
        exception_type="AMBIGUOUS",
        reason="two candidates",
        amount_paise=250000,
        leg=str(Leg.PAYMENT_TO_INVOICE),
        candidates=["INV-2026-0001", "INV-2026-0002"],
    )


@pytest.fixture
def events():
    captured: list[dict] = []

    def logger(**kwargs):
        captured.append(kwargs)

    logger.captured = captured  # type: ignore[attr-defined]
    return logger


def test_a_valid_choice_becomes_a_t4_match(events):
    verdict = Verdict(
        decision="match",
        invoice_no="INV-2026-0001",
        reason="Older of two identical invoices; customers clear the oldest first.",
        confidence=0.72,
    )
    adj = Adjudicator(client=StubClient(verdict))
    exc = _exception()
    matches, resolved, calls = adj.adjudicate(_sources(), [exc], events)

    assert calls == 1
    assert len(matches) == 1
    assert matches[0].right_id == "INV-2026-0001"
    assert matches[0].tier == "T4_ADJUDICATED"
    assert matches[0].reason == verdict.reason
    assert id(exc) in resolved


def test_an_invented_invoice_number_is_rejected_not_matched(events):
    """The whole safety argument in one test.

    A model that names an invoice nobody offered must produce no match at all.
    A hallucination has to fail closed -- into the queue -- never into the
    ledger.
    """
    verdict = Verdict(
        decision="match",
        invoice_no="INV-2026-9999",
        reason="Confident and completely made up.",
        confidence=0.99,
    )
    adj = Adjudicator(client=StubClient(verdict))
    exc = _exception()
    matches, resolved, _ = adj.adjudicate(_sources(), [exc], events)

    assert matches == []
    assert resolved == set()
    assert adj.stats.rejected_offlist == 1
    assert any(e["action"] == "reject" for e in events.captured)


def test_a_hedged_answer_falls_below_the_confidence_floor(events):
    verdict = Verdict(
        decision="match",
        invoice_no="INV-2026-0002",
        reason="Could be either, honestly.",
        confidence=0.35,
    )
    adj = Adjudicator(client=StubClient(verdict), min_confidence=0.60)
    matches, resolved, _ = adj.adjudicate(_sources(), [_exception()], events)

    assert matches == []
    assert resolved == set()
    assert adj.stats.declined == 1


def test_an_explicit_decline_leaves_the_row_in_the_queue(events):
    verdict = Verdict(
        decision="decline",
        invoice_no="",
        reason="Both invoices are identical on every field given.",
        confidence=0.5,
    )
    adj = Adjudicator(client=StubClient(verdict))
    matches, resolved, _ = adj.adjudicate(_sources(), [_exception()], events)

    assert matches == []
    assert resolved == set()
    assert any(e["action"] == "decline" for e in events.captured)


def test_an_api_failure_does_not_abort_the_run(events):
    adj = Adjudicator(client=StubClient(RuntimeError("upstream exploded")))
    matches, resolved, _ = adj.adjudicate(_sources(), [_exception()], events)

    assert matches == []
    assert adj.stats.errors == 1
    assert any(e["action"] == "error" for e in events.captured)


def test_the_call_budget_is_enforced(events):
    verdict = Verdict(
        decision="match",
        invoice_no="INV-2026-0001",
        reason="Fine.",
        confidence=0.9,
    )
    adj = Adjudicator(client=StubClient(verdict), max_calls=1)
    exceptions = [_exception(), _exception(), _exception()]
    matches, _, calls = adj.adjudicate(_sources(), exceptions, events)

    assert calls == 1
    assert len(matches) == 1
    assert any(e["action"] == "skip" for e in events.captured)


def test_only_ambiguous_rows_with_candidates_are_offered(events):
    adj = Adjudicator(client=StubClient())
    unrelated = Exception_(
        entity_type="settlement",
        entity_id="setl_9",
        exception_type="MISSING_CREDIT",
        reason="no credit",
        leg=str(Leg.SETTLEMENT_TO_BANK),
    )
    matches, resolved, calls = adj.adjudicate(_sources(), [unrelated], events)

    assert (matches, resolved, calls) == ([], set(), 0)
    assert adj.stats.considered == 0


def test_missing_credentials_skip_rather_than_guess(events, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    adj = Adjudicator()
    matches, resolved, calls = adj.adjudicate(_sources(), [_exception()], events)

    assert (matches, resolved, calls) == ([], set(), 0)
    assert any(e["actor"] == "llm:unavailable" for e in events.captured)


def test_the_prompt_carries_both_candidates_verbatim(events):
    verdict = Verdict(
        decision="decline", invoice_no="", reason="Symmetric.", confidence=0.4
    )
    client = StubClient(verdict)
    Adjudicator(client=client).adjudicate(_sources(), [_exception()], events)

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "INV-2026-0001" in prompt
    assert "INV-2026-0002" in prompt
    assert client.messages.calls[0]["output_format"] is Verdict
