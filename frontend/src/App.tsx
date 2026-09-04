import { useEffect, useState } from "react";
import { api, type RunSummary, type Scorecard } from "./api";
import { count } from "./format";
import { ScorecardView } from "./screens/ScorecardView";
import { ExceptionsView } from "./screens/ExceptionsView";
import { MatchesView } from "./screens/MatchesView";
import { AuditView } from "./screens/AuditView";

type Tab = "scorecard" | "exceptions" | "matches" | "audit";

const TABS: { id: Tab; label: string; blurb: string }[] = [
  { id: "scorecard", label: "Scorecard", blurb: "Precision, recall, and what being wrong cost" },
  { id: "exceptions", label: "Exceptions", blurb: "What the engine refused to match, worst first" },
  { id: "matches", label: "Matches", blurb: "Every link, and the rule that made it" },
  { id: "audit", label: "Audit trail", blurb: "Every decision in order, machine and human" },
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

  const active = TABS.find((t) => t.id === tab)!;

  return (
    <div className="flex min-h-full">
      {/* ------------------------------------------------------- sidebar */}
      <aside className="sticky top-0 hidden h-screen w-[236px] flex-none flex-col border-r border-line bg-card/70 px-4 py-5 lg:flex">
        <div className="flex items-center gap-2.5 px-2">
          <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-brand-bg text-[15px]">
            📗
          </span>
          <span className="text-[15px] font-semibold tracking-tight">
            LedgerStein
          </span>
        </div>

        <nav className="mt-8 flex flex-col gap-0.5">
          {TABS.map((option) => (
            <button
              key={option.id}
              onClick={() => setTab(option.id)}
              className={`label rounded-[10px] px-3 py-2.5 text-left transition ${
                tab === option.id
                  ? "bg-card-3 text-ink"
                  : "text-mute hover:bg-card-2 hover:text-ink-2"
              }`}
            >
              {option.label}
            </button>
          ))}
        </nav>

        {run && (
          <div className="mt-auto rounded-[14px] bg-card-2 px-3.5 py-3.5">
            <div className="label text-mute">This run</div>
            <dl className="mt-2.5 space-y-1.5 text-[12.5px]">
              <div className="flex justify-between gap-3">
                <dt className="text-mute">Batch</dt>
                <dd className="num text-ink">{run.batch}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-mute">Rows</dt>
                <dd className="num text-ink">{count(run.rows)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-mute">Matched</dt>
                <dd className="num text-good">{count(run.match_count)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-mute">Queued</dt>
                <dd className="num text-warn">{count(run.exception_count)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-mute">Took</dt>
                <dd className="num text-ink">{run.duration_seconds.toFixed(2)}s</dd>
              </div>
              {run.llm_calls > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-mute">AI calls</dt>
                  <dd className="num text-brand-ink">{run.llm_calls}</dd>
                </div>
              )}
            </dl>
          </div>
        )}
      </aside>

      {/* ---------------------------------------------------------- main */}
      <div className="min-w-0 flex-1">
        <header className="border-b border-line bg-sand/80 px-5 py-4 backdrop-blur sm:px-7">
          <div className="mx-auto flex max-w-[1320px] flex-wrap items-center gap-x-5 gap-y-3">
            <div className="min-w-0">
              <h1 className="text-[19px] leading-tight font-semibold tracking-tight">
                {active.label}
              </h1>
              <p className="mt-0.5 text-[13px] text-mute">{active.blurb}</p>
            </div>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <select
                value={batch}
                onChange={(event) => setBatch(event.target.value)}
                className="num rounded-full border border-line bg-card px-3.5 py-2 text-[12.5px] text-ink outline-none"
              >
                {batches.map((option) => (
                  <option key={option.name} value={option.name}>
                    {option.name}
                  </option>
                ))}
              </select>

              <label
                className={`flex cursor-pointer items-center gap-2 rounded-full border px-3.5 py-2 text-[12.5px] transition ${
                  useLlm
                    ? "border-brand bg-brand-bg text-brand-ink"
                    : "border-line bg-card text-ink-2"
                }`}
              >
                <input
                  type="checkbox"
                  checked={useLlm}
                  onChange={(event) => setUseLlm(event.target.checked)}
                  className="accent-brand"
                />
                Adjudicator
              </label>

              <button
                onClick={reconcile}
                disabled={busy}
                className="rounded-full bg-brand px-5 py-2 text-[12.5px] font-semibold text-white transition hover:brightness-105 disabled:opacity-50"
              >
                {busy ? "Reconciling…" : "Reconcile"}
              </button>
            </div>
          </div>

          {/* Tabs stay reachable when the sidebar is hidden. */}
          <nav className="mx-auto mt-4 flex max-w-[1320px] gap-1.5 lg:hidden">
            {TABS.map((option) => (
              <button
                key={option.id}
                onClick={() => setTab(option.id)}
                className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
                  tab === option.id
                    ? "border-brand bg-brand-bg font-medium text-brand-ink"
                    : "border-line bg-card text-ink-2"
                }`}
              >
                {option.label}
              </button>
            ))}
          </nav>
        </header>

        <main className="mx-auto max-w-[1320px] px-5 py-6 sm:px-7">
          {error && (
            <div className="mb-5 rounded-card border border-bad/30 bg-bad-bg px-4 py-3 text-[13.5px] text-bad">
              {error}
            </div>
          )}

          {!run ? (
            <div className="rounded-card border border-line bg-card px-6 py-20 text-center soft-shadow">
              <h2 className="text-[19px] font-semibold">Nothing reconciled yet</h2>
              <p className="mx-auto mt-2 max-w-md text-[13.5px] leading-relaxed text-mute">
                Pick a batch and reconcile it. LedgerStein reads the ERP ledger,
                the gateway exports and the bank statement, then reports what it
                matched, what it refused to match, and why.
              </p>
            </div>
          ) : (
            <>
              {tab === "scorecard" &&
                (card ? (
                  <ScorecardView card={card} />
                ) : (
                  <div className="rounded-card border border-line bg-card px-6 py-14 text-center text-[13.5px] leading-relaxed text-mute soft-shadow">
                    This batch shipped no ground truth, so there is nothing to
                    score against — which is the real-world case. Work the
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
