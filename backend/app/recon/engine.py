"""Run the four legs in order and record every decision.

The engine owns two responsibilities the legs deliberately do not: sequencing,
so a later leg can see what an earlier one already claimed, and the audit trail,
so no decision -- including the decision to give up on a row -- leaves the
system unrecorded.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .legs import (
    LegResult,
    match_bank_to_invoices,
    match_payments_to_invoices,
    match_payments_to_settlements,
    match_settlements_to_bank,
)
from .model import AuditEvent, Exception_, Leg, Match, Sources, Tier
from .normalize import classify_operating, load_sources


@dataclass
class ReconResult:
    """Everything one reconciliation run produced."""

    run_id: str
    batch: str
    started_at: datetime
    duration_seconds: float
    sources: Sources
    matches: list[Match] = field(default_factory=list)
    exceptions: list[Exception_] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    classifications: dict[str, str] = field(default_factory=dict)
    """Bank rows recognised as ordinary operating traffic rather than breaks."""

    llm_calls: int = 0

    # ------------------------------------------------------------- summaries

    def matches_for(self, leg: Leg) -> list[Match]:
        return [m for m in self.matches if m.leg == leg]

    def tier_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for match in self.matches:
            mix[str(match.tier)] = mix.get(str(match.tier), 0) + 1
        return dict(sorted(mix.items()))

    def exception_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for exc in self.exceptions:
            mix[exc.exception_type] = mix.get(exc.exception_type, 0) + 1
        return dict(sorted(mix.items(), key=lambda kv: -kv[1]))

    def exception_value_paise(self) -> int:
        """Rupees sitting in the unresolved pile. The number a CFO asks for."""
        return sum(e.amount_paise for e in self.exceptions)

    def throughput(self) -> float:
        rows = self.sources.row_count()
        return rows / self.duration_seconds if self.duration_seconds else 0.0


class Engine:
    """Sequences the legs, keeps the audit log, and never overwrites a match.

    ``adjudicator`` is optional and, when supplied, is offered only the rows the
    deterministic tiers declined. It is handed a candidate whitelist and its
    answer is validated against that whitelist before it is believed.
    """

    def __init__(self, adjudicator=None) -> None:
        self.adjudicator = adjudicator
        self._sequence = 0
        self._audit: list[AuditEvent] = []

    # ----------------------------------------------------------------- audit

    def _log(
        self,
        actor: str,
        action: str,
        leg: str,
        subject: str,
        detail: str,
        confidence: float = 0.0,
    ) -> None:
        self._sequence += 1
        self._audit.append(
            AuditEvent(
                sequence=self._sequence,
                at=datetime.now(),
                actor=actor,
                action=action,
                leg=leg,
                subject=subject,
                detail=detail,
                confidence=confidence,
            )
        )

    def _absorb(self, result: LegResult) -> LegResult:
        for match in result.matches:
            self._log(
                actor="rule:" + match.rule,
                action="match",
                leg=str(match.leg),
                subject="%s -> %s" % (match.left_id, match.right_id),
                detail=match.reason,
                confidence=match.confidence,
            )
        for exc in result.exceptions:
            self._log(
                actor="engine",
                action="flag",
                leg=exc.leg,
                subject=exc.entity_id,
                detail="%s: %s" % (exc.exception_type, exc.reason),
            )
        return result

    # ------------------------------------------------------------------- run

    def run(self, sources: Sources, batch: str = "", run_id: str = "") -> ReconResult:
        started = datetime.now()
        clock = time.perf_counter()

        self._sequence = 0
        self._audit = []
        self._log(
            actor="engine",
            action="start",
            leg="",
            subject=batch or "batch",
            detail="Loaded %d rows: %d invoices, %d payments, %d settlements, "
            "%d bank lines."
            % (
                sources.row_count(),
                len(sources.invoices),
                len(sources.payments),
                len(sources.settlements),
                len(sources.bank_txns),
            ),
        )

        leg1 = self._absorb(match_settlements_to_bank(sources))
        leg2 = self._absorb(match_payments_to_settlements(sources))
        leg3 = self._absorb(match_payments_to_invoices(sources))

        # Leg 4 only looks at credits leg 1 did not already claim, so a payout
        # is never also read as a direct customer transfer.
        claimed = {m.right_id for m in leg1.matches}
        claimed.update(
            e.entity_id for e in leg1.exceptions if e.entity_type == "bank_txn"
        )
        leg4 = self._absorb(match_bank_to_invoices(sources, claimed))

        matches = leg1.matches + leg2.matches + leg3.matches + leg4.matches
        exceptions = (
            leg1.exceptions + leg2.exceptions + leg3.exceptions + leg4.exceptions
        )

        classifications = dict(leg1.notes)
        for txn in sources.bank_txns:
            label = classify_operating(txn.narration)
            if label and txn.txn_id not in classifications:
                classifications[txn.txn_id] = label
        for txn_id, label in classifications.items():
            self._log(
                actor="rule:narration_classifier",
                action="classify",
                leg="",
                subject=txn_id,
                detail="Ordinary operating traffic (%s), excluded from the "
                "exception queue." % label,
            )

        exceptions.extend(self._overdue_receivables(sources, matches))

        llm_calls = 0
        if self.adjudicator is not None:
            extra_matches, resolved, llm_calls = self.adjudicator.adjudicate(
                sources=sources, exceptions=exceptions, logger=self._log
            )
            matches.extend(extra_matches)
            exceptions = [e for e in exceptions if id(e) not in resolved]

        duration = time.perf_counter() - clock
        self._log(
            actor="engine",
            action="finish",
            leg="",
            subject=batch or "batch",
            detail="%d matches, %d exceptions, %.3fs."
            % (len(matches), len(exceptions), duration),
        )

        return ReconResult(
            # Second resolution alone collides when two runs start inside the
            # same second -- a double-click on Reconcile is enough -- and the
            # collision surfaces as a primary key violation on persist.
            run_id=run_id
            or "run_%s_%s"
            % (started.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:6]),
            batch=batch,
            started_at=started,
            duration_seconds=duration,
            sources=sources,
            matches=matches,
            exceptions=exceptions,
            audit=list(self._audit),
            classifications=classifications,
            llm_calls=llm_calls,
        )

    def _overdue_receivables(
        self, sources: Sources, matches: list[Match]
    ) -> list[Exception_]:
        """Invoices nothing ever paid.

        Not a reconciliation break -- the books are consistent -- but it is the
        money the merchant is actually missing, so it belongs on the same queue.
        """
        settled = {
            m.right_id
            for m in matches
            if m.leg in (Leg.PAYMENT_TO_INVOICE, Leg.BANK_TO_INVOICE)
        }
        today = max((i.due_date for i in sources.invoices), default=None)
        out: list[Exception_] = []
        for invoice in sources.invoices:
            if invoice.invoice_no in settled:
                continue
            overdue = today is not None and invoice.due_date < today
            out.append(
                Exception_(
                    entity_type="invoice",
                    entity_id=invoice.invoice_no,
                    exception_type="OVERDUE_INVOICE" if overdue else "AMBIGUOUS",
                    reason="No payment and no bank credit found against %s "
                    "(%s, due %s)."
                    % (invoice.invoice_no, invoice.customer_name, invoice.due_date),
                    amount_paise=invoice.amount_paise,
                    leg=str(Leg.PAYMENT_TO_INVOICE),
                )
            )
        for exc in out:
            self._log(
                actor="engine",
                action="flag",
                leg=exc.leg,
                subject=exc.entity_id,
                detail="%s: %s" % (exc.exception_type, exc.reason),
            )
        return out


def reconcile_directory(directory: Path, adjudicator=None) -> ReconResult:
    """Convenience entry point: load a generated batch and reconcile it."""
    directory = Path(directory)
    sources = load_sources(directory)
    return Engine(adjudicator=adjudicator).run(sources, batch=directory.name)
