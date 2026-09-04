# Submission — Razorpay AI Buildathon

## Track

**Track 04 — AI Finance Controller.**
*Run the books and the cash position.*

## Project name

**LedgerStein**

## What it solves

A merchant's money leaves three separate trails and none of them agree. The ERP
knows what was billed. Razorpay knows what was collected, what it charged, and
what it paid out. The bank knows what actually landed. Nobody knows all three.

Closing that gap is a manual, monthly, spreadsheet job, and the parts that eat
the time are never the clean rows — they are the T+2 settlement skew, the credit
that is short by the gateway fee plus 18% GST on that fee, the three payouts that
arrived as one lump sum carrying only the first one's UTR, the customer who
NEFTed straight to the bank so the ERP never heard about it, and the refund that
clawed back from a later payout so `net` stopped equalling `gross − fee − tax`.

LedgerStein reconciles all three sources in one pass across four joins, and then
does the part that actually matters: it produces a **typed, priced, explained
list of what it refused to close**. Every match carries the rule that made it and
a sentence a finance controller can read. Every decision — including the
decisions to give up, and every human override — lands in an append-only audit
trail.

**Measured on a held-out batch the engine was never tuned against:**

| | |
|---|---|
| Precision | **100.0%** — 476 of 476 matches correct |
| Recall | **98.6%** — 7 true links not found |
| Cost of being wrong | **₹0** across 0 false matches |
| Throughput | ~24,000 source rows/second |
| Exception queue | 93 rows, ₹1.17 Cr exposure — 85 correct declines, 8 genuine misses |
| Tests | 46 passing |

The 8 misses are the point, not an embarrassment: they are payments that fit two
open invoices from the same customer equally well, for the same amount, days
apart. Nothing in the exports separates them. LedgerStein declines them and shows
both candidates, which is the correct answer.

## GitHub repo

**https://github.com/AbdulMalik1287/ledgerstein** — public.

Full functionality and architecture reference: [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).

## 5-minute pitch video

> **Not yet recorded.** Record, upload unlisted, and replace this line with the URL.

Word-for-word script, screen-by-screen directions, a pre-flight checklist and the
exact rows to point at: **[docs/PITCH_SCRIPT.md](PITCH_SCRIPT.md)**.

Cut summary:

| Time | Beat | Show |
|---|---|---|
| 0:00–0:35 | **The problem, concretely.** Three CSVs open side by side that disagree. Point at one settlement, its bank credit, and the ₹2,500 gap between them. | The raw data |
| 0:35–1:10 | **One pass.** Hit Reconcile. 620 rows, 0.025s, 476 matched. | Dashboard header |
| 1:10–2:00 | **Precision before recall.** The scorecard. Lead with 100% precision and ₹0 lost, then recall. Explain why that order is the honest one — a missing match sits in a queue, a wrong match closes a book. | Scorecard tab |
| 2:00–3:00 | **The queue is the product.** Sort by exposure. Open the biggest `AMBIGUOUS` row, show both candidates, resolve it. | Exceptions tab |
| 3:00–3:40 | **Nothing is a black box.** Search that payment id in the audit trail; show the machine decisions and your own resolution in one sequence. | Audit tab |
| 3:40–4:30 | **The failure handled well.** Tell the crossed-reference story from §6 below — 9 false matches, ₹7.96 lakh, and the corroboration rule that fixed it. | `CROSSED_REFERENCE` rows |
| 4:30–5:00 | **Why the numbers are trustworthy.** Tuned on batch A, reported on batch B. The generator writes the answer key; the engine never reads it. | Terminal scorecard |

---

## What broke, and how we got out

Eight real ones, in the order they happened.

### 1. The benchmark was too easy, and it was flattering us

**Symptom.** The engine scored **100% precision and 100% recall on both
batches** — including the held-out one. That should have felt good. It felt
wrong.

**Diagnosis.** The score was measuring the generator, not the matcher. Every
defect we had seeded — timing skew, fee netting, merged payouts, split payments,
mistyped references — was *hard but solvable*: enough arithmetic and text
normalisation and a rule gets there. We had built a benchmark out of exactly the
problems we knew how to solve.

**Fix.** Three injectors in `app/gen/ambiguity.py` that create situations where
the right answer is **not derivable from the data at all**:

- **Twin invoices** — clone the invoice a reference-less payment really paid into
  a second invoice, same customer, same amount, issued a day apart. Both are now
  consistent with the payment.
- **Crossed references** — make a payment quote a *real* invoice belonging to a
  different customer.
- **Shaved credits** — deduct a flat correspondent-bank charge so the credit is
  neither the net amount nor within rounding tolerance.

**Outcome.** Recall dropped to 96.1%, then settled at 98.6% after the real fixes
below. Recall below 100% is now the *correct* answer, and the residue is
documented rather than tuned away. `--ambiguity 0` still generates the easy batch
if you want to see the difference.

**Lesson.** If a metric is perfect, suspect the measurement before congratulating
the system.

### 2. The precision trap that cost ₹7.96 lakh

**Symptom.** The moment crossed references went in, precision fell from 100% to
95.3% — **9 false matches worth ₹7,96,016**.

**Diagnosis.** Leg 3 was trusting a quoted invoice number on its own. A
transposed digit does not always land on nothing — sometimes it lands on another
customer's *live* invoice, and then an exact-reference rule produces a match that
is confident, fully explainable, and completely wrong. That is the most expensive
failure mode a reconciliation engine has, because it looks correct in the audit
trail.

**Fix.** Corroboration. Before accepting a reference, check the payer actually
owns the invoice they quoted, routing from the email on a payment through the ERP
customer master to the customer id on an invoice. Where the payer does not own
it, the reference is rejected, a `CROSSED_REFERENCE` exception is raised so a
human learns the customer is quoting the wrong number, and the payment falls
through to the amount-and-date rules — which recover almost all of them
correctly.

**Outcome.** Precision back to 100%, ₹0 in false matches, and all 10 crossed
references in batch B recovered to the right invoice.

### 3. Two customers, one AP mailbox

**Symptom.** 10 payments on leg 3 that clearly should have matched were being
written off as `UNBILLED_RECEIPT`. Every one of them had an exact amount match
against exactly one open invoice for that customer.

**Diagnosis.** The email-to-customer index was a `dict[str, Customer]`. The
generator was building emails from the company's first word, so *Meridian
Textiles* and *Meridian Foods* both got `accounts@meridian.co.in` — and the dict
silently kept only the last one. Every payment from the shadowed company resolved
to the **wrong customer**, so the candidate pool came back empty.

**Fix.** Two changes, because this is a real-world condition, not just a
generator bug. The generator now gives each customer a distinct mailbox but
deliberately routes one in eight through a shared group address, since group
companies genuinely share an AP inbox. And the index became
`dict[str, list[Customer]]`, with the matcher taking the union of invoices across
every customer on that mailbox and still requiring the amount match to be unique.

**Outcome.** Leg 3 recall went from 95.1% to 100% on batch A.

**Lesson.** A one-to-one index over data that is one-to-many fails silently and
in the direction that looks like a matcher weakness.

### 4. Settlements netted to exactly zero

**Symptom.** Leg 1 recall stuck at 92.6%. Two settlements matched nothing at all,
and the diagnostic showed both had `net_paise` of **0**, pointing at bank rows
with a credit of **0**.

**Diagnosis.** A refund clawback larger than the payout it landed on was being
clamped with `min(clawback, gross - fee - tax)`, driving `net` to exactly zero.
The generator then emitted a ₹0.00 "credit" — and `is_credit` is
`credit_paise > 0`, so those rows were not credits at all and never entered the
candidate pool. Artefact, not defect.

**Fix.** Model what actually happens: a clawback bigger than the payout it lands
on is **deferred to the next payout**, not clamped. Banks do not post nil-value
credits, and a payout netted to zero leaves a settlement row with no statement
line to reconcile against. Added `test_no_settlement_is_netted_to_zero` and
`test_every_bank_row_moves_money` so the class of bug cannot come back.

**Outcome.** Leg 1 to 100%.

### 5. Merged payouts, stranded twice

**Symptom.** Five settlements flagged `MISSING_CREDIT` when the money had
plainly arrived.

**Diagnosis, part one.** A consolidated bank credit carries only the *first*
payout's UTR. T1 matched that one settlement and marked the credit claimed —
stranding its siblings, which then had no candidate large enough to match.

**Fix, part one.** A residual pass: for a credit that already has matches,
compute `credit − sum(matched payouts)` and look for a unique set of remaining
payouts summing to that leftover.

**Diagnosis, part two.** The fix changed nothing. The residual pass was being
handed `open_credits` — the list with claimed credits already filtered out — so
it could never see the very credits it existed to examine.

**Fix, part two.** Pass every credit, and let the pass distinguish claimed from
unclaimed itself.

**Outcome.** Leg 1 recall 82.8% → 96.6% → 100%.

**Lesson.** When a fix produces no change at all, suspect it never ran before
suspecting it was wrong.

### 6. A metric that lied in our own disfavour

**Symptom.** The dashboard reported **17 "missed a real link"** while the per-leg
table right above it showed **7 missed**.

**Diagnosis.** The grader counted an exception as a miss whenever the entity had
a truth link on *any* leg. But `CROSSED_REFERENCE` is advisory — the row gets
flagged so a human knows the customer is quoting the wrong invoice, *and* it gets
matched correctly by another rule. A row that ended up matched was not given up
on.

**Fix.** Grade per leg, and exclude entities that were matched on that same leg.

**Outcome.** 85 correct declines, 8 genuine misses — now consistent with the leg
table.

**Lesson.** This error understated us, and we still had to fix it. A metric that
is wrong in your favour and one that is wrong against you are the same bug.

### 7. A stale server that made a correct fix look broken

**Symptom.** Fixed the grader, restarted the API, reloaded the dashboard — still
17. Fixed it again. Still 17.

**Diagnosis.** `pkill -f uvicorn` silently fails against Python processes on
Windows. The old server kept the port, the new one died with
`[Errno 10048] only one usage of each socket address`, and the browser kept
talking to the pre-fix process. The log line was there; we had not read it.

**Fix.** Kill by port owner via `Get-NetTCPConnection` / `Stop-Process`, and read
the server log before concluding anything about the code.

**Lesson.** Before debugging the fix, confirm the fix is the thing running.

---

### Two calls that avoided breakage

**Stdlib-only reconciliation core.** Python 3.14 was new enough that pandas and
rapidfuzz wheels were a real risk, and a mid-build dependency failure would have
cost hours. `difflib` and plain dicts do the job. The side benefit: the ~24,000
rows/second figure is the engine's own rather than borrowed from a C extension.

**Integer paise everywhere.** No float ever touches an amount. A reconciliation
engine that is off by a paisa because of binary rounding is worse than useless,
because the error stays invisible until it is large.

### 8. No Anthropic key, at an AI buildathon

**Symptom.** Tier 4 was written against Anthropic and there was no key to run it
with. The engine scored 100%/98.6% with **zero LLM calls** — defensible as
architecture, but a tier that has never fired is hard to distinguish from one
that does not work.

**Diagnosis.** The coupling was accidental rather than necessary. Every guarantee
that makes the tier safe to act on — the candidate whitelist, the confidence
floor, the call budget, the audit entry — already lived *outside* the model call,
in `adjudicator.py`. Only the twenty lines that actually spoke to Anthropic cared
which vendor it was.

**Fix.** Extracted `providers.py`. A backend now only has to turn two prompts
into a dict; it cannot widen what the tier is allowed to believe. Anthropic keeps
the official SDK, and Gemini and Groq were added over plain HTTP with `httpx` —
already a dependency, and two POST requests did not justify two more SDKs in the
image. Both have a free tier that needs no card. `auto` picks the first backend
with a key set, and whichever answered is named in the audit trail as the actor.

**Outcome.** The tier runs at ₹0. Nine new tests pin the request shape and
response parsing for each backend against a mock transport, and the nine
existing safety tests pass unchanged against the new injection point — which is
the evidence that the guards really were independent of the vendor.

**Lesson.** A dependency that blocks you is worth checking for necessity before
working around it. This one turned out to be a twenty-line seam.

### Still open

**Tier 4 has not made a live call yet.** Every backend is exercised against a
mock transport rather than the real API, so the request shapes are verified but
the servers' acceptance of them is not. Set `GEMINI_API_KEY` or `GROQ_API_KEY`
(both free, no card) and rerun with `--llm` to exercise it against the six
`AMBIGUOUS` rows in `batch_b`. Without a key the tier skips and records why, so
the 100%/98.6% figures above remain pure deterministic matching.
