# 5-minute pitch — script and screen directions

Spoken word count: **~720**, which lands near 5:00 at a normal 145–150 wpm with
demo pauses. Read it slightly slower than feels natural; the numbers need room.

All figures and IDs below are real rows in `batch_b`. If you regenerate with a
different seed they will change — regenerate with `--seed 4291` or update the
IDs here.

---

## Pre-flight

Do all of this **before** you hit record.

```bash
# 1. Fresh batches (skip if data/generated already exists)
cd backend
python -m app.gen.generate --seed 7    --invoices 240                --out ../data/generated/batch_a
python -m app.gen.generate --seed 4291 --invoices 260 --customers 21 --out ../data/generated/batch_b

# 2. Wipe old runs so the dashboard opens clean
rm -f ledgerstein.sqlite3

# 3. Start both
python -m uvicorn app.main:app --port 8000
cd ../frontend && npm run dev
```

**Windows set-up**

| | |
|---|---|
| Browser window | 1600 × 1000, zoom 100% |
| Tabs open | ① `http://localhost:5173` ② `docs/ARCHITECTURE.md` on GitHub (backup only) |
| Editor | `data/generated/batch_b/` open with `pg_settlements.csv` and `bank_statement.csv` in a **split view**, side by side |
| Terminal | `cd backend`, cleared, ready to type — dark theme, font ≥ 16px |
| Dashboard state | **Do not reconcile yet.** It should say "No run yet". |
| Silence | Notifications off. Close Slack, mail, everything. |

**Two things to find in the CSVs before recording** so you can scroll straight to
them:

- `bank_statement.csv` → row **`TXN000040`** (a short credit)
- `pg_settlements.csv` → row **`setl_t3jvzydzzoe0h4`** (the payout it belongs to)

---

## Beat 1 · The disagreement — `0:00 – 0:32`

> **Screen:** Editor, split view. `pg_settlements.csv` left, `bank_statement.csv`
> right. Both scrolled to the rows named above. Highlight the two amounts as you
> say them.

**VO**

> "This is a merchant's Razorpay settlement report, and this is their bank
> statement for the same day.
>
> Razorpay says it paid out thirty-four thousand, seven hundred and forty-nine
> rupees and ninety-four paise. The bank shows thirty-two thousand, two hundred
> and forty-nine — the same UTR, the same date, two and a half thousand rupees
> short.
>
> Neither file is wrong. A correspondent bank took a cut, and nothing in either
> export says so. Multiply that by a month, across three systems that each know
> one third of the story, and that's a finance team's week."

*(83 words)*

---

## Beat 2 · One pass — `0:32 – 1:04`

> **Screen:** Switch to the dashboard tab. Empty state visible for a beat.
> **Click `Reconcile`.** Let the header numbers land before you keep talking.

**VO**

> "LedgerStein reads all three — the ERP invoice ledger, the Razorpay payments
> and settlements, and the bank statement — and joins them across four legs.
>
> Six hundred and twenty rows. Twenty-five milliseconds. Four hundred and
> seventy-six matches, and ninety-three things it refused to match.
>
> That second number is the product. Anyone can report a match rate. The
> question a controller actually has is *what did you skip, and how much is it
> worth*."

*(74 words)*

---

## Beat 3 · Precision before recall — `1:04 – 1:52`

> **Screen:** Scorecard tab (it opens here by default). Point at the tiles left
> to right. Then drop to the **"Which tier earned it"** panel on the right.

**VO**

> "Precision is a hundred percent. Four hundred and seventy-six matches, four
> hundred and seventy-six correct, zero rupees lost to a wrong one.
>
> Recall is ninety-eight point six. Precision sits to the left of recall on this
> screen on purpose — a missing match sits in a queue, a wrong match closes a
> book. They are not the same failure and averaging them hides the expensive one.
>
> This panel breaks it down by tier. Eighty-two percent came from exact key
> matches — free, certain. The inferred tier did eight percent, and it also
> scored a hundred percent precision, so it isn't buying recall by guessing.
>
> And note the last row. The AI tier made zero calls. Everything you're looking
> at is deterministic."

*(112 words)*

---

## Beat 4 · The queue is the product — `1:52 – 2:58`

> **Screen:** **Exceptions tab.** Let the filter chips read for a second — they
> show count and rupee exposure per type. Then click the **top row**
> (`pay_p8aqc68xw55ck7`, ₹16,88,601). The detail panel opens on the right.
> Click a candidate chip, type into the box, click **Record decision**.

**VO**

> "Ninety-three rows, one crore seventeen lakh of exposure, sorted by what it's
> worth — not by when it arrived. A queue that buries the row that matters under
> forty small ones is a queue nobody works.
>
> Every row carries a diagnosis, not just 'unmatched'. Money that may be gone:
> a payout with no credit. A credit posted twice. A credit that's short.
> Judgement calls: a customer quoting somebody else's invoice number. And things
> that aren't errors at all — this payment settles after the statement ends,
> so it's flagged and explicitly marked *not a break*.
>
> Here's the biggest one. Sixteen lakh, eighty-eight thousand, no reference on
> the payment, and it fits two open invoices from the same customer equally
> well — same amount, issued a day apart. Nothing in the exports separates them.
>
> So the engine declines, and hands you both candidates. I pick one, say why,
> and record it."

*(140 words)*

---

## Beat 5 · Nothing is a black box — `2:58 – 3:32`

> **Screen:** **Audit trail tab.** Paste `pay_p8aqc68xw55ck7` into the search
> box. Your `human:controller` resolution appears. Then clear the search and
> type `merged_payout` to show a machine decision.

**VO**

> "That decision is now in the audit trail, as `human:controller`, in the same
> sequence as the engine's own — because a person overruling the machine is a
> decision that has to be exactly as reviewable.
>
> And the machine's decisions read the same way. This one: a bank credit was
> larger than the payout its UTR named, and the leftover matched two other
> payouts exactly. One credit, three settlements, one visible UTR — and a
> sentence explaining it.
>
> Six hundred and twenty events for six hundred and twenty rows. Every match,
> every flag, every decline."

*(95 words)*

---

## Beat 6 · What broke — `3:32 – 4:22`

> **Screen:** Stay on Exceptions. Click the **`CROSSED_REFERENCE`** filter chip.
> Click `pay_ii53kddqi6a1cy` to show the reason text in the detail panel.

**VO**

> "The most useful thing that happened during this build was a failure.
>
> Early on it scored a hundred percent on precision *and* recall, on a held-out
> batch. That felt like success. It was the benchmark measuring itself — every
> defect I'd seeded was one I already knew how to solve.
>
> So I seeded a case I couldn't solve: a payment quoting a real invoice number
> that belongs to a *different customer*. A transposed digit doesn't always land
> on nothing.
>
> It cost nine false matches, seven lakh ninety-six thousand rupees — and every
> one of them looked perfectly explainable in the audit trail. That's the worst
> failure this kind of system has.
>
> The fix was corroboration. Before trusting a reference, check the payer
> actually owns the invoice they quoted. These rows are that check firing: the
> reference is rejected, a human is told the customer is quoting the wrong
> number, and the payment still gets matched correctly by amount and date."

*(155 words)*

---

## Beat 7 · Why the numbers hold — `4:22 – 5:00`

> **Screen:** Switch to the **terminal**. Type and run:
> ```
> python -m app.cli reconcile ../data/generated/batch_b
> ```
> Let the scorecard print. Hold on it while you finish.

**VO**

> "Two batches. Every rule was written and tuned against batch A. Every number
> I've shown you is batch B, which the engine has never been tuned against.
>
> The generator writes the answer key. The engine never reads it — only the
> scorecard does.
>
> And the AI tier only sees what the rules refused. On this batch it looked at
> six rows and declined four of them — 'same customer, same amount, same PO
> reference, one day apart.' It added nothing to recall, which is the right
> answer, because those rows genuinely cannot be decided.
>
> The first time I ran it live, it did make a match. A wrong one, worth one lakh
> sixteen thousand. It had reasoned from the ERP's paid flag — a field this
> whole system exists to distrust, and which my own prompt had told it to use.
> That's fixed, and it's in the write-up.
>
> Precision first. Costs in rupees. An honest exception list. That's
> LedgerStein."

*(165 words)*

> **If you have a key in the demo:** tick **Adjudicator** before hitting
> Reconcile in Beat 2, and the audit trail in Beat 5 will carry
> `llm:gemini/gemini-3.6-flash` decline lines you can point at. Budget ~85s for
> six rows, so start the run before you begin talking. Without a key the tier
> logs a skip and everything else is unchanged.

---

## Page-by-page cheat sheet

Tape this next to your monitor.

| Time | Where you are | What you do |
|---|---|---|
| 0:00 | **Editor** — two CSVs, split | Highlight `TXN000040` and `setl_t3jvzydzzoe0h4` |
| 0:32 | **Dashboard** — empty state | Click **Reconcile** |
| 1:04 | **Scorecard tab** | Tiles left→right, then the tier panel |
| 1:52 | **Exceptions tab** | Read the chips, click the top row |
| 2:30 | **Exceptions** — detail panel | Pick a candidate, type, **Record decision** |
| 2:58 | **Audit trail tab** | Search `pay_p8aqc68xw55ck7`, then `merged_payout` |
| 3:32 | **Exceptions tab** | Filter `CROSSED_REFERENCE`, open `pay_ii53kddqi6a1cy` |
| 4:22 | **Terminal** | Run the CLI scorecard, hold on the output |
| — | *(optional)* | With a key: `--llm` shows the four declines live |

---

## The numbers you must not fumble

| Say | Not |
|---|---|
| "one hundred percent precision" | "a hundred percent accurate" |
| "ninety-eight point six recall" | "ninety-nine percent" |
| "zero rupees lost to a false match" | "no errors" |
| "nine false matches, seven lakh ninety-six thousand" | "about eight lakh" |
| "six hundred and twenty rows in twenty-five milliseconds" | "instantly" |

---

## Rows you can point at

| What | ID | Value |
|---|---|---|
| Short credit (opening) | `TXN000040` vs `setl_t3jvzydzzoe0h4` | ₹2,500.00 short |
| Second short credit | `TXN000081` | ₹999.11 short |
| Biggest ambiguous | `pay_p8aqc68xw55ck7` | ₹16,88,601 · `INV-2026-0073` vs `INV-2026-9261` |
| Crossed reference | `pay_ii53kddqi6a1cy` | quotes `INV-2026-0075` (Westbrook Foods) · recovered to `INV-2026-0004` |
| Merged payout | `TXN000036` = ₹24,91,587.54 | three payouts: ₹3,30,222.29 + ₹16,33,998.24 + ₹5,27,367.01 |
| Missing credit | `setl_0tvzalc1byxmwv` | ₹5,79,991.00 never arrived |

---

## If you overrun

Cut in this order. Each is self-contained.

1. **Beat 5's second half** (the `merged_payout` search) — saves ~15s.
2. **Beat 3's tier breakdown** — saves ~20s. Keep the precision/recall framing.
3. **Beat 4's exception-type tour** ("Money that may be gone… Judgement calls…")
   — saves ~25s. Go straight from the sort order to the biggest row.

**Never cut Beat 6.** The failure story is the one thing in this video another
team is unlikely to have.
