"""Score a reconciliation run against the batch's ground truth.

Three rules govern this module.

Precision is reported before recall, because a wrong match costs more than a
missing one -- a missing match sits in a queue, a wrong match closes a book.

False matches are reported in rupees as well as counts, because five wrong
matches on five-rupee rows and five wrong matches on five-lakh rows are not the
same failure.

Exceptions are graded, not just counted. Declining to match a row that genuinely
has no partner is correct behaviour and is scored as such; declining a row that
did have a partner is a miss and is scored as one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .engine import ReconResult
from .model import Leg

LEG_TRUTH_KEYS: dict[Leg, str] = {
    Leg.SETTLEMENT_TO_BANK: "leg1_settlement_to_bank",
    Leg.PAYMENT_TO_SETTLEMENT: "leg2_payment_to_settlement",
    Leg.PAYMENT_TO_INVOICE: "leg3_payment_to_invoice",
    Leg.BANK_TO_INVOICE: "leg4_bank_to_invoice",
}


@dataclass
class LegScore:
    leg: str
    true_links: int
    predicted: int
    correct: int
    wrong: int
    missed: int
    wrong_value_paise: int
    missed_value_paise: int
    wrong_examples: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.correct / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.true_links if self.true_links else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "leg": self.leg,
            "true_links": self.true_links,
            "predicted": self.predicted,
            "correct": self.correct,
            "wrong": self.wrong,
            "missed": self.missed,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "wrong_value_rupees": self.wrong_value_paise / 100,
            "missed_value_rupees": self.missed_value_paise / 100,
            "wrong_examples": self.wrong_examples[:5],
        }


@dataclass
class Scorecard:
    batch: str
    rows: int
    duration_seconds: float
    throughput_rows_per_second: float
    legs: list[LegScore]
    tier_mix: dict[str, int]
    tier_scores: dict[str, dict]
    """Precision per tier. A headline match rate hides which tier earned it --
    and whether the cheap certain rules or the expensive guessy ones are
    carrying the number."""
    exception_mix: dict[str, int]
    exceptions_justified: int
    exceptions_missed: int
    exception_value_paise: int
    llm_calls: int

    @property
    def overall(self) -> LegScore:
        return LegScore(
            leg="overall",
            true_links=sum(l.true_links for l in self.legs),
            predicted=sum(l.predicted for l in self.legs),
            correct=sum(l.correct for l in self.legs),
            wrong=sum(l.wrong for l in self.legs),
            missed=sum(l.missed for l in self.legs),
            wrong_value_paise=sum(l.wrong_value_paise for l in self.legs),
            missed_value_paise=sum(l.missed_value_paise for l in self.legs),
        )

    def as_dict(self) -> dict:
        overall = self.overall
        return {
            "batch": self.batch,
            "rows": self.rows,
            "duration_seconds": round(self.duration_seconds, 4),
            "throughput_rows_per_second": round(self.throughput_rows_per_second, 1),
            "llm_calls": self.llm_calls,
            "overall": overall.as_dict(),
            "legs": [l.as_dict() for l in self.legs],
            "tier_mix": self.tier_mix,
            "tier_scores": self.tier_scores,
            "exceptions": {
                "total": self.exceptions_justified + self.exceptions_missed,
                "justified": self.exceptions_justified,
                "missed_a_real_link": self.exceptions_missed,
                "value_rupees": self.exception_value_paise / 100,
                "by_type": self.exception_mix,
            },
        }


def load_truth(directory: Path) -> dict:
    return json.loads((Path(directory) / "truth.json").read_text(encoding="utf-8"))


def score(result: ReconResult, truth: dict) -> Scorecard:
    legs: list[LegScore] = []
    amounts = _amount_index(result)
    tiers: dict[str, dict] = {}

    for leg, key in LEG_TRUTH_KEYS.items():
        expected: dict[str, str] = truth.get(key, {})
        predicted = result.matches_for(leg)

        # Legs 1 and 4 are many-to-one in truth (several payouts, one credit),
        # so compare on the pair rather than assuming a unique right-hand side.
        true_pairs = {(left, right) for left, right in expected.items()}
        predicted_pairs = [(m.left_id, m.right_id) for m in predicted]

        for match in predicted:
            bucket = tiers.setdefault(
                str(match.tier),
                {"predicted": 0, "correct": 0, "wrong": 0, "wrong_value_paise": 0},
            )
            bucket["predicted"] += 1
            if (match.left_id, match.right_id) in true_pairs:
                bucket["correct"] += 1
            else:
                bucket["wrong"] += 1
                bucket["wrong_value_paise"] += amounts.get(match.left_id, 0)

        correct = [p for p in predicted_pairs if p in true_pairs]
        wrong = [p for p in predicted_pairs if p not in true_pairs]
        found_left = {p[0] for p in correct}
        missed = [p for p in true_pairs if p[0] not in found_left]

        legs.append(
            LegScore(
                leg=str(leg),
                true_links=len(true_pairs),
                predicted=len(predicted_pairs),
                correct=len(correct),
                wrong=len(wrong),
                missed=len(missed),
                wrong_value_paise=sum(amounts.get(left, 0) for left, _ in wrong),
                missed_value_paise=sum(amounts.get(left, 0) for left, _ in missed),
                wrong_examples=[
                    "%s -> %s (truth: %s)"
                    % (left, right, expected.get(left, "no link"))
                    for left, right in wrong
                ],
            )
        )

    for bucket in tiers.values():
        bucket["precision"] = round(
            bucket["correct"] / bucket["predicted"] if bucket["predicted"] else 0.0, 4
        )
        bucket["wrong_value_rupees"] = bucket.pop("wrong_value_paise") / 100

    justified, missed_link = _grade_exceptions(result, truth)

    return Scorecard(
        batch=result.batch,
        rows=result.sources.row_count(),
        duration_seconds=result.duration_seconds,
        throughput_rows_per_second=result.throughput(),
        legs=legs,
        tier_mix=result.tier_mix(),
        tier_scores=dict(sorted(tiers.items())),
        exception_mix=result.exception_mix(),
        exceptions_justified=justified,
        exceptions_missed=missed_link,
        exception_value_paise=result.exception_value_paise(),
        llm_calls=result.llm_calls,
    )


def _amount_index(result: ReconResult) -> dict[str, int]:
    """Every entity id to its rupee value, for costing mistakes."""
    index: dict[str, int] = {}
    for s in result.sources.settlements:
        index[s.settlement_id] = s.net_paise
    for p in result.sources.payments:
        index[p.payment_id] = p.amount_paise
    for i in result.sources.invoices:
        index[i.invoice_no] = i.amount_paise
    for t in result.sources.bank_txns:
        index[t.txn_id] = t.amount_paise
    return index


def _grade_exceptions(result: ReconResult, truth: dict) -> tuple[int, int]:
    """Split the queue into correct declines and genuine misses.

    Graded against the exception's own leg, not against every leg. A payment
    that settled but was never invoiced has a leg 2 link and no leg 3 link, so
    flagging it as an unbilled receipt on leg 3 is right, and counting that as
    a miss because leg 2 knew about it would be scoring the wrong question.
    """
    linked_by_leg: dict[str, set[str]] = {
        str(leg): set(truth.get(key, {}).keys())
        for leg, key in LEG_TRUTH_KEYS.items()
    }
    # Leg 4 keys on the bank row, and leg 1 consumes bank rows too, so a
    # duplicate credit flagged on leg 1 must not be judged against leg 4.
    justified = 0
    missed = 0
    for exc in result.exceptions:
        expected = linked_by_leg.get(exc.leg, set())
        if exc.entity_id in expected:
            missed += 1
        else:
            justified += 1
    return justified, missed


def render(card: Scorecard) -> str:
    """A scorecard a judge can read without opening a JSON viewer."""
    lines: list[str] = []
    overall = card.overall
    lines.append("Kosh reconciliation scorecard -- batch %s" % card.batch)
    lines.append("=" * 68)
    lines.append(
        "%d source rows in %.3fs (%.0f rows/sec), %d LLM calls"
        % (
            card.rows,
            card.duration_seconds,
            card.throughput_rows_per_second,
            card.llm_calls,
        )
    )
    lines.append("")
    lines.append(
        "%-28s %7s %7s %7s %7s %7s"
        % ("leg", "true", "prec", "recall", "wrong", "missed")
    )
    lines.append("-" * 68)
    for leg in card.legs:
        lines.append(
            "%-28s %7d %6.1f%% %6.1f%% %7d %7d"
            % (
                leg.leg.replace("leg1_", "1 ").replace("leg2_", "2 ")
                .replace("leg3_", "3 ").replace("leg4_", "4 ").replace("_", " "),
                leg.true_links,
                leg.precision * 100,
                leg.recall * 100,
                leg.wrong,
                leg.missed,
            )
        )
    lines.append("-" * 68)
    lines.append(
        "%-28s %7d %6.1f%% %6.1f%% %7d %7d"
        % (
            "OVERALL",
            overall.true_links,
            overall.precision * 100,
            overall.recall * 100,
            overall.wrong,
            overall.missed,
        )
    )
    lines.append("")
    lines.append(
        "Cost of being wrong: Rs %.2f across %d false matches"
        % (overall.wrong_value_paise / 100, overall.wrong)
    )
    lines.append("")
    lines.append("Matches by tier:")
    for tier, stats in card.tier_scores.items():
        lines.append(
            "  %-16s %4d matched, %5.1f%% precision, Rs %.2f wrong"
            % (
                tier,
                stats["predicted"],
                stats["precision"] * 100,
                stats["wrong_value_rupees"],
            )
        )
    lines.append("")
    lines.append(
        "Exception queue: %d rows worth Rs %.2f"
        % (
            card.exceptions_justified + card.exceptions_missed,
            card.exception_value_paise / 100,
        )
    )
    lines.append(
        "  correctly declined %d, missed a real link %d"
        % (card.exceptions_justified, card.exceptions_missed)
    )
    for kind, count in card.exception_mix.items():
        lines.append("  %-22s %4d" % (kind, count))
    return "\n".join(lines)
