import { useEffect, useState } from "react";
import { api, type RunSummary, type Scorecard } from "./api";
import { count } from "./format";
import { Button } from "./ui";
import { ScorecardView } from "./screens/ScorecardView";
import { ExceptionsView } from "./screens/ExceptionsView";
import { MatchesView } from "./screens/MatchesView";
import { AuditView } from "./screens/AuditView";

type Tab = "scorecard" | "exceptions" | "matches" | "audit";

const TABS: { id: Tab; label: string; blurb: string }[] = [
  {
    id: "scorecard",
    label: "Scorecard",
    blurb: "Precision, recall, and what being wrong cost",
  },
  {
    id: "exceptions",
    label: "Exceptions",
    blurb: "What the engine refused to match, worst first",
  },
  {
    id: "matches",
    label: "Matches",
    blurb: "Every link, and the rule that made it",
  },
  {
    id: "audit",
    label: "Audit trail",
    blurb: "Every decision in order, machine and human",
  },
];

export default function App() {
  const [batches, setBatches] = useState<{ name: string }[]>([]);
  const [batch, setBatch] = useState("batch_b");
  const [useLlm, setUseLlm] = useState(false);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [card, setCard] = useState<Scorecard | null>(null);
  const [tab, setTab] = useState<Tab>("scorecard");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [available, runs] = await Promise.all([api.batches(), api.runs()]);
        setBatches(available);
        if (available.length) setBatch(available[available.length - 1].name);
        if (runs.length) await select(runs[0].id);
      } catch (problem) {
        setError(String(problem));
      }
    })();
  }, []);

  const select = async (id: string) => {
    const detail = await api.run(id);
    setRun(detail.summary);
    setCard(detail.scorecard);
    setBatch(detail.summary.batch);
  };

  const reconcile = async () => {
    setBusy(true);
    setError("");
    try {
      const summary = await api.createRun(batch, useLlm);
      await select(summary.id);
      setTab("scorecard");
    } catch (problem) {
      setError(String(problem));
    } finally {
      setBusy(false);
    }
  };

  const active = TABS.find((option) => option.id === tab)!;

  return (
    <div className="flex min-h-full">
      {/* ------------------------------------------------------- sidebar */}
      <aside className="sticky top-0 hidden h-screen w-[228px] flex-none flex-col border-r border-line bg-raised px-3.5 py-6 lg:flex">
        <div className="px-2.5">
          <span className="text-[16px] font-semibold tracking-[-0.02em] text-ink">
            LedgerStein
          </span>
          <p className="mt-0.5 text-[11.5px] text-faint">Finance controller</p>
        </div>

        <nav className="mt-9 flex flex-col gap-0.5">
          {TABS.map((option) => (
            <button
              key={option.id}
              onClick={() => setTab(option.id)}
              aria-current={tab === option.id ? "page" : undefined}
              className={`rounded-input px-2.5 py-2 text-left text-[13px] ${
                tab === option.id
                  ? "bg-accent-soft font-medium text-ink"
                  : "text-ink-2 hover:bg-sunk hover:text-ink"
              }`}
            >
              {option.label}
            </button>
          ))}
        </nav>

        {run && (
          <dl className="mt-auto space-y-2 border-t border-line pt-4 text-[12px]">
            <Row label="Batch" value={run.batch} />
            <Row label="Rows" value={count(run.rows)} />
            <Row label="Matched" value={count(run.match_count)} tone="text-good" />
            <Row
              label="Queued"
              value={count(run.exception_count)}
              tone="text-warn"
            />
            <Row label="Elapsed" value={`${run.duration_seconds.toFixed(2)}s`} />
            {run.llm_calls > 0 && (
              <Row label="AI calls" value={count(run.llm_calls)} />
            )}
          </dl>
        )}
      </aside>

      {/* ---------------------------------------------------------- main */}
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 border-b border-line bg-bg/85 px-5 py-4 backdrop-blur sm:px-8">
          <div className="mx-auto flex max-w-[1340px] flex-wrap items-center gap-x-6 gap-y-3">
            <div className="min-w-0">
              <h1 className="text-[20px] leading-tight font-semibold tracking-[-0.022em]">
                {active.label}
              </h1>
              <p className="mt-0.5 text-[12.5px] text-ink-2">{active.blurb}</p>
            </div>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="batch">
                Batch
              </label>
              <select
                id="batch"
                value={batch}
                onChange={(event) => setBatch(event.target.value)}
                className="num rounded-full border border-line bg-surface px-3.5 py-2 text-[12.5px] text-ink outline-none hover:bg-raised"
              >
                {batches.map((option) => (
                  <option key={option.name} value={option.name}>
                    {option.name}
                  </option>
                ))}
              </select>

              <label
                className={`flex cursor-pointer items-center gap-2 rounded-full border px-3.5 py-2 text-[12.5px] ${
                  useLlm
                    ? "border-accent bg-accent-soft text-ink"
                    : "border-line bg-surface text-ink-2 hover:bg-raised"
                }`}
              >
                <input
                  type="checkbox"
                  checked={useLlm}
                  onChange={(event) => setUseLlm(event.target.checked)}
                  className="accent-accent"
                />
                Adjudicator
              </label>

              <Button onClick={reconcile} disabled={busy}>
                {busy ? "Reconciling" : "Reconcile"}
              </Button>
            </div>
          </div>

          <nav className="mx-auto mt-4 flex max-w-[1340px] gap-1.5 lg:hidden">
            {TABS.map((option) => (
              <button
                key={option.id}
                onClick={() => setTab(option.id)}
                aria-current={tab === option.id ? "page" : undefined}
                className={`rounded-full border px-3 py-[5px] text-[12px] ${
                  tab === option.id
                    ? "border-accent bg-accent font-medium text-surface"
                    : "border-line bg-surface text-ink-2"
                }`}
              >
                {option.label}
              </button>
            ))}
          </nav>
        </header>

        <main className="mx-auto max-w-[1340px] px-5 py-7 sm:px-8">
          {error && (
            <div
              role="alert"
              className="mb-5 rounded-card border border-bad/25 bg-bad-bg px-4 py-3 text-[13px] text-bad"
            >
              {error}
            </div>
          )}

          {!run ? (
            <div className="rounded-card border border-line bg-surface px-6 py-20 text-center lift">
              <h2 className="text-[18px] font-semibold tracking-[-0.015em]">
                Nothing reconciled yet
              </h2>
              <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-ink-2">
                Choose a batch and reconcile it. LedgerStein reads the ERP
                ledger, the gateway exports and the bank statement, then reports
                what it matched, what it refused to match, and why.
              </p>
            </div>
          ) : (
            <>
              {tab === "scorecard" &&
                (card ? (
                  <ScorecardView card={card} />
                ) : (
                  <div className="rounded-card border border-line bg-surface px-6 py-14 text-center text-[13px] leading-relaxed text-ink-2 lift">
                    This batch shipped no ground truth, so there is nothing to
                    score against. That is the real-world case. Work the
                    exception queue instead.
                  </div>
                ))}
              {tab === "exceptions" && <ExceptionsView runId={run.id} />}
              {tab === "matches" && <MatchesView runId={run.id} />}
              {tab === "audit" && <AuditView runId={run.id} />}
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  tone = "text-ink",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-faint">{label}</dt>
      <dd className={`num ${tone}`}>{value}</dd>
    </div>
  );
}
