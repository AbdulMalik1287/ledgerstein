"""Command line entry point.

    python -m app.cli reconcile ../data/generated/batch_a
    python -m app.cli reconcile ../data/generated/batch_b --json report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recon.engine import reconcile_directory
from .recon.metrics import load_truth, render, score


def cmd_reconcile(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    adjudicator = None
    if args.llm:
        from .recon.adjudicator import Adjudicator

        adjudicator = Adjudicator(
            provider=args.provider,
            model=args.model,
            max_calls=args.max_llm_calls,
        )

    result = reconcile_directory(directory, adjudicator=adjudicator)

    truth_path = directory / "truth.json"
    if not truth_path.exists():
        print(
            "Reconciled %d rows in %.3fs: %d matches, %d exceptions."
            % (
                result.sources.row_count(),
                result.duration_seconds,
                len(result.matches),
                len(result.exceptions),
            )
        )
        return 0

    card = score(result, load_truth(directory))
    print(render(card))

    if args.json:
        Path(args.json).write_text(
            json.dumps(card.as_dict(), indent=2), encoding="utf-8"
        )
        print("\nWrote %s" % args.json)
    if args.audit:
        Path(args.audit).write_text(
            "\n".join(
                "%04d  %-34s %-9s %-28s %s"
                % (e.sequence, e.actor, e.action, e.subject, e.detail)
                for e in result.audit
            ),
            encoding="utf-8",
        )
        print("Wrote %s (%d events)" % (args.audit, len(result.audit)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledgerstein")
    sub = parser.add_subparsers(dest="command", required=True)

    recon = sub.add_parser("reconcile", help="reconcile a generated batch")
    recon.add_argument("directory")
    recon.add_argument("--json", help="write the scorecard as JSON")
    recon.add_argument("--audit", help="write the audit trail as text")
    recon.add_argument(
        "--llm", action="store_true", help="let the adjudicator see the residue"
    )
    recon.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "anthropic", "gemini", "groq"],
        help="model backend; auto takes the first with a key set",
    )
    recon.add_argument(
        "--model", default="", help="override the backend's default model"
    )
    recon.add_argument("--max-llm-calls", type=int, default=25)
    recon.set_defaults(func=cmd_reconcile)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
