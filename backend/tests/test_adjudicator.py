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


class StubBackend:
    """Stands in for a model. Records what it was asked, returns what it was told.

    Injected where a real provider would go, so every guard under test runs the
    same code path it runs in production -- the guards live around the call, not
    inside it, which is the whole reason the backend is swappable.
    """

    name = "stub"
    model = "stub-1"

    def __init__(self, *verdicts) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> dict:
        self.calls.append((system, prompt))
        nxt = self._verdicts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt.model_dump()


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
    adj = Adjudicator(backend=StubBackend(verdict))
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
    adj = Adjudicator(backend=StubBackend(verdict))
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
    adj = Adjudicator(backend=StubBackend(verdict), min_confidence=0.60)
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
    adj = Adjudicator(backend=StubBackend(verdict))
    matches, resolved, _ = adj.adjudicate(_sources(), [_exception()], events)

    assert matches == []
    assert resolved == set()
    assert any(e["action"] == "decline" for e in events.captured)


def test_an_api_failure_does_not_abort_the_run(events):
    adj = Adjudicator(backend=StubBackend(RuntimeError("upstream exploded")))
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
    adj = Adjudicator(backend=StubBackend(verdict), max_calls=1)
    exceptions = [_exception(), _exception(), _exception()]
    matches, _, calls = adj.adjudicate(_sources(), exceptions, events)

    assert calls == 1
    assert len(matches) == 1
    assert any(e["action"] == "skip" for e in events.captured)


def test_only_ambiguous_rows_with_candidates_are_offered(events):
    adj = Adjudicator(backend=StubBackend())
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
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    adj = Adjudicator()
    matches, resolved, calls = adj.adjudicate(_sources(), [_exception()], events)

    assert (matches, resolved, calls) == ([], set(), 0)
    assert any(e["actor"] == "llm:unavailable" for e in events.captured)


def test_the_prompt_carries_both_candidates_verbatim(events):
    verdict = Verdict(
        decision="decline", invoice_no="", reason="Symmetric.", confidence=0.4
    )
    backend = StubBackend(verdict)
    Adjudicator(backend=backend).adjudicate(_sources(), [_exception()], events)

    system, prompt = backend.calls[0]
    assert "INV-2026-0001" in prompt
    assert "INV-2026-0002" in prompt
    # The rule the whole tier rests on has to reach the model as well as being
    # enforced after it.
    assert "only choose an invoice_no that appears in the candidate list" in system
