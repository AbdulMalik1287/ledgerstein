# LedgerStein — AI Finance Controller

> Three-way reconciliation across a merchant's ERP, payment gateway and bank,
> with an honest exception list.

**Razorpay AI Buildathon — Track 04: AI Finance Controller**

---

## Result

On a held-out batch the engine has never been tuned against:

| | |
|---|---|
| **Precision** | **100.0%** — 476 of 476 matches correct |
| **Recall** | **98.6%** — 7 true links not found |
| **Cost of being wrong** | **₹0** across 0 false matches |
| **Throughput** | ~24,000 source rows/second, 620 rows in 0.025s |
| **Exception queue** | 93 rows worth ₹1.17 Cr — 85 correctly declined, 8 genuine misses |

Reproduce it in three commands (see [Running it](#running-it)). The scorecard
is computed against ground truth the generator emits, so none of these numbers
are asserted by hand.

The residue is not a rounding error, it is the point. Those 8 misses are
payments that fit two open invoices from the same customer equally well, for
the same amount, days apart. Nothing in the exports separates them. LedgerStein
declines them and shows both candidates, which is the correct answer.

---

## The problem

A merchant's money leaves three different trails and none of them agree:

| Source | System of record for | What it does *not* know |
|---|---|---|
| **ERP invoice ledger** | what the merchant expected to collect | whether it ever landed, and net of what |
| **Razorpay payments + settlements** | what the gateway collected and paid out | invoice context, and non-gateway receipts |
| **Bank statement** | what actually credited the current account | which invoices or payments a credit belongs to |

Finance teams close this gap by hand, in a spreadsheet, monthly.

## What LedgerStein does

```
ERP invoice ──leg 3── PG payment ──leg 2── PG settlement ──leg 1── bank credit
   (what we      (who paid,       (what was batched      (what actually
    billed)       and for what)    and netted)            hit the account)

                        └────────── leg 4 ──────────┘
                   (NEFT straight to the bank, bypassing the gateway)
```

Four joins, each a ladder of rules from cheap-and-certain to costly-and-
uncertain. A rule only ever sees what the rules above it could not resolve, so
a cheap certain match is never overridden by an expensive plausible one.

| Tier | Method | Cost | Handles |
|---|---|---|---|
| **T1** | exact key match (UTR, settlement id, invoice number) | free | the clean ~82% |
| **T2** | derived amount (`net = gross − fee − GST − refunds`) inside a dated window | free | fee netting, T+2 skew, paise-level rounding |
| **T3** | bounded subset-sum, fuzzy references, customer-and-amount inference | cheap | merged payouts, split payments, mistyped refs |
| **T4** | LLM adjudicator, **restricted to a candidate whitelist** | metered | genuinely ambiguous residue only |

### Why the LLM tier is safe to act on

T4 is handed a small candidate set and can only choose from it. The check runs
in code *after* the call, not as a request in the prompt — so a hallucinated
identifier becomes a **rejected response**, not a wrong match. It may also
decline, and declining is the expected answer when the evidence really is
symmetric. A confidence floor drops hedged answers, a call budget caps spend,
and a run without an API key skips the tier and logs why rather than guessing.

That guarantee is tested rather than claimed — see
[`tests/test_adjudicator.py`](backend/tests/test_adjudicator.py), in particular
`test_an_invented_invoice_number_is_rejected_not_matched`.

### Everything is explained

Every match carries the rule that made it, a sentence a finance controller can
read, and a confidence. Every decision — including the decisions to give up,
and every human override — lands in an append-only audit trail:

```
0002  rule:utr_exact              match   setl_5ue00m3wumbpbq -> TXN000002
      UTR HDFCN26155995456 appears in the narration of TXN000002.

0026  rule:merged_payout_residual match   setl_94yd8o2jyr29vk -> TXN000036
      Credit TXN000036 exceeds the payout its UTR names by Rs 21,61,365.25,
      which is exactly this payout and 1 other.

0029  engine                      flag    TXN000040
      VALUE_VARIANCE: Credit is Rs 2,500.00 short of the 1 payout(s) matched to
      it. The link is certain -- the UTR proves it -- so this is a deduction to
      chase, not a mismatch to re-match.

0621  human:abdul                 resolve pay_p8aqc68xw55ck7
      AMBIGUOUS resolved as: Confirmed with the customer's AP team.
      (linked to INV-2026-0073)
```

## The benchmark is adversarial on purpose

The generator emits three mismatched sources plus a `truth.json` answer key,
seeded with defects a finance team actually chases every month:

`TIMING_SKEW` · `FEE_MISMATCH` · `MERGED_PAYOUT` · `SPLIT_PAYMENT` ·
`REFUND_ADJUSTMENT` · `DUPLICATE_CREDIT` · `MISSING_CREDIT` ·
`CHARGEBACK_DEBIT` · `ROUNDING_DRIFT` · `DIRECT_TRANSFER` · `TYPO_REF` ·
`MISSING_REF` · `MISSING_SETTLEMENT_LINK` · `UNBILLED_RECEIPT` ·
`PENDING_SETTLEMENT` · `OVERDUE_INVOICE`

**An early version scored 100% precision and 100% recall on both batches.** That
measured the benchmark, not the matcher: every seeded defect was solvable given
enough arithmetic. Three injectors in
[`app/gen/ambiguity.py`](backend/app/gen/ambiguity.py) fixed it:

- **Twin invoices** — a reference-less payment that fits two identical open
  invoices. Irreducibly ambiguous; the right output is a decline with both
  candidates.
- **Crossed references** — a payment quoting a *real* invoice belonging to a
  different customer. A transposed digit does not always land on nothing. This
  cost 9 false matches worth ₹7.96 L until leg 3 began corroborating the payer
  against the invoice owner.
- **Shaved credits** — a correspondent bank deducting a flat charge, so the
  credit is neither the net amount nor within rounding tolerance.

Generate an easy batch with `--ambiguity 0` to see the difference for yourself.

## Honest metrics

Three rules govern [`app/recon/metrics.py`](backend/app/recon/metrics.py):

- **Precision is reported before recall.** A missing match sits in a queue; a
  wrong match closes a book.
- **False matches are priced in rupees**, not just counted. Five wrong matches
  on five-rupee rows and five on five-lakh rows are not the same failure.
- **The exception queue is graded, not just counted.** Declining a row that
  genuinely has no partner is correct behaviour and scores as such. Declining
  one that did have a partner is a miss and scores as one.

Precision is also reported **per tier**, because a headline match rate hides
whether the cheap certain rules or the expensive inferred ones earned it.

## Running it

```bash
# 1. Backend
cd backend
python -m venv ../.venv
# Windows: ../.venv/Scripts/activate    macOS/Linux: source ../.venv/bin/activate
python -m pip install -r requirements.txt
python -m app.gen.generate --seed 7    --invoices 240                --out ../data/generated/batch_a
python -m app.gen.generate --seed 4291 --invoices 260 --customers 21 --out ../data/generated/batch_b

# Score it from the terminal
python -m app.cli reconcile ../data/generated/batch_b
python -m app.cli reconcile ../data/generated/batch_b --audit trail.txt --json card.json

# Optionally let the adjudicator see the ambiguous residue
export ANTHROPIC_API_KEY=...
python -m app.cli reconcile ../data/generated/batch_b --llm

# 2. API
python -m uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs

# 3. Dashboard
cd ../frontend && npm install && npm run dev # http://localhost:5173
```

Tests: `cd backend && python -m pytest` — 37 passing, covering generator
invariants, the adjudicator's safety properties, and the API end to end.

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — functionality and architecture
  reference: the four legs, all seventeen rules, the exception taxonomy, the LLM
  safety contract, the data model, and the API.
- **[docs/SUBMISSION.md](docs/SUBMISSION.md)** — Buildathon submission, including
  a write-up of the seven things that broke during the build and how each was
  diagnosed.
- **[docs/PITCH_SCRIPT.md](docs/PITCH_SCRIPT.md)** — the 5-minute demo script:
  spoken narration, screen directions, and the exact rows to point at.

## Repo layout

```
backend/
  app/
    gen/       synthetic three-source generator, defect taxonomy, ambiguity injectors
    recon/     normalize · legs · engine · adjudicator · metrics
    db.py      SQLite persistence for runs, matches, exceptions, audit
    main.py    FastAPI service
    cli.py     terminal scorecard
  tests/       37 tests
frontend/      React + Vite + Tailwind dashboard
data/          generated batches (gitignored)
docs/          architecture reference, submission write-up, demo script
```

## Design notes

- **Money is integer paise everywhere.** A reconciliation engine that is off by
  a paisa because of binary rounding is worse than useless — the error is
  invisible until it is large.
- **The core is stdlib-only.** No pandas, no rapidfuzz, so the throughput
  figures are the engine's own rather than borrowed from a C extension.
- **Subset-sum is bounded to three members and returned only when unique.**
  Over an unbounded pool it will find *a* combination for almost any number,
  and an ambiguous one is a confident wrong answer.
- **Ordinary operating traffic is classified, not queued.** Interest credits,
  vendor payouts and payroll debits are not reconciliation failures; letting
  them pile up is the fastest way to build a queue nobody reads.

## Licence

MIT.
