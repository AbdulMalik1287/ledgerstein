# Kosh — AI Finance Controller

> कोष *(kosh)* — treasury.
> Three-way reconciliation across a merchant's ERP, payment gateway and bank, with an honest exception list.

**Razorpay AI Buildathon — Track 04: AI Finance Controller**

---

## The problem

A merchant's money leaves three different trails and none of them agree:

| Source | System of record for | What it does *not* know |
|---|---|---|
| **ERP invoice ledger** | what the merchant expected to collect | whether it ever landed, and net of what |
| **Razorpay payments + settlements** | what the gateway collected and paid out | invoice context, and non-gateway receipts |
| **Bank statement** | what actually credited the current account | which invoices or payments a credit belongs to |

Finance teams close this gap by hand, in a spreadsheet, monthly. Kosh closes it in a
single pass and — the part that matters — tells you honestly which rows it *could not*
resolve and why.

## What Kosh does

1. **Ingests** three mismatched sources (synthetic, generated here; the schemas mirror
   real Razorpay settlement reports and Indian bank statement exports).
2. **Reconciles** them across three legs with a tiered matcher — cheap deterministic
   rules first, an LLM only on the residue.
3. **Reports** a match rate, per-leg precision and recall against known ground truth,
   throughput, and a typed exception queue.
4. **Explains** every single decision. Each match carries the tier that made it, a
   human-readable reason, and a confidence. Nothing is a black box.

### The three legs

```
ERP invoice ──leg 3── PG payment ──leg 2── PG settlement ──leg 1── bank credit
   (what we      (who paid,       (what was batched      (what actually
    billed)       and for what)    and netted)            hit the account)
```

### The matcher tiers

| Tier | Method | Cost | Handles |
|---|---|---|---|
| **T1** | exact key match (UTR, payment_id, invoice_no) | free | the clean 60–70% |
| **T2** | derived amount (`net = gross − fee − GST`) within a date window | free | fee/tax netting, T+2 settlement skew |
| **T3** | bounded subset-sum + fuzzy narration match | cheap | merged payouts, split payments, typo'd refs |
| **T4** | LLM adjudicator, **restricted to a candidate whitelist** | metered | genuinely ambiguous residue only |

T4 cannot invent an ID. It is handed a small candidate set and must pick from it or
decline — so a hallucination becomes a *declined* match, never a wrong one.

### Exception types

Anything the tiers can't resolve lands in a typed queue rather than being silently
dropped or force-matched:

`TIMING_SKEW` · `FEE_MISMATCH` · `MERGED_PAYOUT` · `SPLIT_PAYMENT` ·
`REFUND_REVERSAL` · `DUPLICATE_CREDIT` · `MISSING_CREDIT` · `CHARGEBACK_DEBIT` ·
`ROUNDING_DRIFT` · `DIRECT_TRANSFER` · `ORPHAN_PAYMENT` · `UNBILLED_RECEIPT` ·
`OVERDUE_INVOICE` · `AMBIGUOUS`

## Honest metrics

The generator emits `truth.json` alongside the CSVs, so every linkage has a known
correct answer. Kosh is tuned on batch A and **reported on a held-out batch B it has
never seen**. The scorecard includes the number nobody volunteers: false matches, and
the rupee value sitting behind them.

## Status

Under active development for the Buildathon. See `docs/` for design notes.

## Repo layout

```
backend/
  app/
    gen/      synthetic three-source generator + ground truth
    recon/    normalize, tiers, engine, LLM adjudicator, metrics, audit
    api/      FastAPI routes
  tests/
frontend/     React + Vite dashboard
data/         generated batches (gitignored)
docs/         design notes
```

## Licence

MIT.
