"""Tier 4: ask a model about the rows the rules refused to decide.

This tier exists for one shape of problem -- a payment that fits two open
invoices equally well on amount, customer and date. No arithmetic separates
them. What might separate them is judgement: which invoice a customer is more
likely to have been paying, given the ageing, the purchase order reference and
what the ERP already believes.

Three constraints make that safe to act on.

The model is handed a **candidate whitelist** and can only choose from it. It
cannot name an invoice that was not offered, so a hallucinated id becomes a
rejected response rather than a wrong match. The check is enforced here, after
the call, not merely requested in the prompt.

The model is allowed to **decline**, and declining is the expected answer when
the evidence really is symmetric. A tier that must produce an answer will
produce a wrong one.

Every call is **logged and counted** -- inputs, decision, reason, confidence --
and the tier is bounded by ``max_calls`` so a large batch cannot quietly turn
into a large bill.

The backend is pluggable (see ``providers.py``) because those four guarantees
live out here, around the call, rather than inside it. Swapping Anthropic for
Gemini or Groq cannot widen what this tier is allowed to believe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from . import providers
from .model import Exception_, Leg, Match, Sources, Tier

SYSTEM_PROMPT = """You are the final tier of a payment reconciliation engine \
for an Indian merchant. Deterministic rules have already resolved everything \
they safely could. You see only what they refused to decide.

You will be given one payment and a short list of candidate invoices. Choose \
the invoice the payment most likely settles, or decline.

Rules you must follow:

1. You may only choose an invoice_no that appears in the candidate list. Never \
   invent, correct, or extrapolate an identifier.
2. Decline whenever the candidates are genuinely indistinguishable on the \
   evidence given. A declined row goes to a human, which is a good outcome. A \
   wrong match closes a book, which is an expensive one.
3. Weigh real signals only: how long an invoice has been outstanding, whether \
   the ERP already marks one as paid, purchase order references, and the gap \
   between issue date and capture date. Customers usually clear the oldest \
   open invoice first, but that is a tendency and not a rule.
4. Your confidence must reflect how much the evidence actually separates the \
   candidates. If one is only slightly more likely, say 0.6, not 0.9.
5. Your reason is read by a finance controller. One sentence, concrete, naming \
   the signal you used."""


class Verdict(BaseModel):
    """The shape every backend must answer in."""

    decision: str = Field(description="Either 'match' or 'decline'.")
    invoice_no: str = Field(
        default="",
        description=(
            "The chosen invoice_no, copied exactly from the candidate list. "
            "Empty string when declining."
        ),
    )
    reason: str = Field(description="One sentence a finance controller can act on.")
    confidence: float = Field(
        description="0 to 1, reflecting how much the evidence separates candidates."
    )


@dataclass
class AdjudicatorStats:
    considered: int = 0
    calls: int = 0
    matched: int = 0
    declined: int = 0
    rejected_offlist: int = 0
    """Responses naming an id that was not on the whitelist. Never acted on."""
    errors: int = 0


class Adjudicator:
    """Bounded LLM tier. Safe to construct without an API key -- it no-ops."""

    def __init__(
        self,
        provider: str = "auto",
        model: str = "",
        max_calls: int = 25,
        min_confidence: float = 0.60,
        backend=None,
    ) -> None:
        self.provider_name = provider
        self.model = model
        self.max_calls = max_calls
        self.min_confidence = min_confidence
        self.stats = AdjudicatorStats()
        self._backend = backend
        self._unavailable = ""

    # --------------------------------------------------------------- backend

    def _get_backend(self):
        """Resolve a model backend, or record why there isn't one.

        ``auto`` takes the first provider with a key present. Which one answered
        goes into the audit trail as the actor, so a run is always traceable to
        the model that made its calls.
        """
        if self._backend is not None:
            return self._backend
        backend, reason = providers.build(self.provider_name, self.model)
        if backend is None:
            self._unavailable = reason
            return None
        self._backend = backend
        self.model = backend.model
        return backend

    # ------------------------------------------------------------ entry point

    def adjudicate(self, sources: Sources, exceptions: list[Exception_], logger):
        """Return ``(matches, resolved_exception_ids, call_count)``.

        Only ``AMBIGUOUS`` exceptions that already carry candidates are offered.
        Everything else is either a correct decline or a finding a human needs
        to see, and neither is improved by asking a model about it.
        """
        matches: list[Match] = []
        resolved: set[int] = set()

        queue = [
            exc
            for exc in exceptions
            if exc.exception_type == "AMBIGUOUS"
            and exc.leg == str(Leg.PAYMENT_TO_INVOICE)
            and len(exc.candidates) >= 2
        ]
        self.stats.considered = len(queue)
        if not queue:
            return matches, resolved, 0

        backend = self._get_backend()
        if backend is None:
            logger(
                actor="llm:unavailable",
                action="skip",
                leg=str(Leg.PAYMENT_TO_INVOICE),
                subject="%d ambiguous rows" % len(queue),
                detail="Adjudication skipped because %s. The rows stay in the "
                "exception queue, which is the safe default." % self._unavailable,
            )
            return matches, resolved, 0

        payments = sources.payment_by_id()
        invoices = sources.invoice_by_no()

        for exc in queue:
            if self.stats.calls >= self.max_calls:
                logger(
                    actor=self._actor(),
                    action="skip",
                    leg=exc.leg,
                    subject=exc.entity_id,
                    detail="Call budget of %d reached; remaining rows left for "
                    "a human." % self.max_calls,
                )
                break

            payment = payments.get(exc.entity_id)
            candidates = [invoices[c] for c in exc.candidates if c in invoices]
            if payment is None or len(candidates) < 2:
                continue

            allowed = {c.invoice_no for c in candidates}
            verdict = self._ask(backend, payment, candidates, logger, exc)
            if verdict is None:
                continue

            # The whitelist check. Enforced here, not trusted to the prompt.
            if verdict.decision != "match" or not verdict.invoice_no:
                self.stats.declined += 1
                logger(
                    actor=self._actor(),
                    action="decline",
                    leg=exc.leg,
                    subject=exc.entity_id,
                    detail=verdict.reason,
                    confidence=verdict.confidence,
                )
                continue

            if verdict.invoice_no not in allowed:
                self.stats.rejected_offlist += 1
                logger(
                    actor=self._actor(),
                    action="reject",
                    leg=exc.leg,
                    subject=exc.entity_id,
                    detail="Model named %r, which was not among the %d "
                    "candidates offered. Response discarded and the row left "
                    "in the queue."
                    % (verdict.invoice_no, len(allowed)),
                    confidence=verdict.confidence,
                )
                continue

            if verdict.confidence < self.min_confidence:
                self.stats.declined += 1
                logger(
                    actor=self._actor(),
                    action="decline",
                    leg=exc.leg,
                    subject=exc.entity_id,
                    detail="Chose %s at confidence %.2f, below the %.2f floor. "
                    "Left in the queue."
                    % (verdict.invoice_no, verdict.confidence, self.min_confidence),
                    confidence=verdict.confidence,
                )
                continue

            self.stats.matched += 1
            matches.append(
                Match(
                    leg=Leg.PAYMENT_TO_INVOICE,
                    left_id=payment.payment_id,
                    right_id=verdict.invoice_no,
                    tier=Tier.T4_ADJUDICATED,
                    rule="llm_adjudicated",
                    reason=verdict.reason,
                    confidence=round(verdict.confidence, 3),
                    amount_paise=payment.amount_paise,
                )
            )
            resolved.add(id(exc))
            logger(
                actor=self._actor(),
                action="match",
                leg=exc.leg,
                subject="%s -> %s" % (payment.payment_id, verdict.invoice_no),
                detail=verdict.reason,
                confidence=verdict.confidence,
            )

        return matches, resolved, self.stats.calls

    # ------------------------------------------------------------------ call

    def _actor(self) -> str:
        """Names the backend that answered, for the audit trail."""
        backend = self._backend
        if backend is None:
            return "llm:%s" % (self.model or self.provider_name)
        return "llm:%s/%s" % (backend.name, backend.model)

    def _ask(self, backend, payment, candidates, logger, exc) -> Verdict | None:
        """One call, one validated verdict, or None.

        Validation lives here so a backend returning the wrong shape is a failed
        call rather than a malformed match. The whitelist check that follows in
        ``adjudicate`` is a separate gate and does not depend on this one.
        """
        prompt = _render_case(payment, candidates)
        self.stats.calls += 1
        try:
            return Verdict.model_validate(backend.complete(SYSTEM_PROMPT, prompt))
        except Exception as error:  # noqa: BLE001 - the tier must never crash a run
            self.stats.errors += 1
            logger(
                actor=self._actor(),
                action="error",
                leg=exc.leg,
                subject=exc.entity_id,
                detail="Adjudication call failed (%s: %s). Row left in the "
                "queue." % (type(error).__name__, error),
            )
            return None


def _render_case(payment, candidates) -> str:
    """The evidence packet. Ids are quoted verbatim so they can be copied back."""
    lines = [
        "PAYMENT",
        json.dumps(
            {
                "payment_id": payment.payment_id,
                "amount_rupees": payment.amount_paise / 100,
                "method": payment.method,
                "captured_at": payment.captured_at.isoformat(sep=" "),
                "invoice_reference_on_payment": payment.invoice_ref or None,
                "payer_email": payment.customer_email,
            },
            indent=2,
        ),
        "",
        "CANDIDATE INVOICES (choose one of these invoice_no values, or decline)",
        json.dumps(
            [
                {
                    "invoice_no": invoice.invoice_no,
                    "customer": invoice.customer_name,
                    "amount_rupees": invoice.amount_paise / 100,
                    "issued": invoice.issue_date.isoformat(),
                    "due": invoice.due_date.isoformat(),
                    "erp_status": invoice.status,
                    "po_ref": invoice.po_ref,
                    "days_outstanding_at_capture": (
                        payment.captured_at.date() - invoice.issue_date
                    ).days,
                }
                for invoice in candidates
            ],
            indent=2,
        ),
    ]
    return "\n".join(lines)
