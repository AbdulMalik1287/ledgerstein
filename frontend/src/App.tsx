import { useEffect, useState } from "react";
import { api, type RunSummary, type Scorecard } from "./api";
import { count, rupeesShort } from "./format";
import { ScorecardView } from "./screens/ScorecardView";
import { ExceptionsView } from "./screens/ExceptionsView";
import { MatchesView } from "./screens/MatchesView";
import { AuditView } from "./screens/AuditView";

type Tab = "scorecard" | "exceptions" | "matches" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "scorecard", label: "Scorecard" },
  { id: "exceptions", label: "Exceptions" },
  { id: "matches", label: "Matches" },
  { id: "audit", label: "Audit trail" },
];

export default function App() {
  const [batches, setBatches] = useState<{ name: string }[]>([]);
  const [batch, setBatch] = useState("batch_a");
  const [useLlm, setUseLlm] = useState(false);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [card, setCard] = useState<Scorecard | null>(null);
  const [tab, setTab] = useState<Tab>("scorecard");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Pick up the most recent run on load so a refresh does not lose the screen.
  useEffect(() => {
    void (async () => {
      try {
        const [available, runs] = await Promise.all([
          api.batches(),
          api.runs(),
        ]);
        setBatches(available);
        if (available.length) setBatch(available[0].name);
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

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line bg-ink-900/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-4 px-5 py-3">
          <div className="flex items-baseline gap-2.5">
            <span className="text-lg font-semibold tracking-tight">Kosh</span>
            <span className="hidden text-xs text-mute sm:inline">
              AI Finance Controller · three-way reconciliation
            </span>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <select
              value={batch}
              onChange={(event) => setBatch(event.target.value)}
              className="num rounded border border-line bg-ink-700 px-2.5 py-1.5 text-xs text-bright outline-none focus:border-accent"
            >
              {batches.map((option) => (
                <option key={option.name} value={option.name}>
                  {option.name}
                </option>
              ))}
            </select>

            <label className="flex cursor-pointer items-center gap-1.5 rounded border border-line bg-ink-700 px-2.5 py-1.5 text-xs text-mute">
              <input
                type="checkbox"
                checked={useLlm}
                onChange={(event) => setUseLlm(event.target.checked)}
                className="accent-accent"
              />
              Adjudicator
            </label>

            <button
              onClick={reconcile}
              disabled={busy}
              className="rounded bg-accent px-3.5 py-1.5 text-xs font-semibold text-ink-900 transition hover:brightness-110 disabled:opacity-50"
            >
              {busy ? "Reconciling…" : "Reconcile"}
            </button>
          </div>
        </div>

        {run && (
          <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-1 border-t border-line px-5 py-2 text-xs text-mute">
            <span className="num text-bright">{run.batch}</span>
            <span>
              <span className="num text-bright">{count(run.rows)}</span> rows
            </span>
            <span>
              <span className="num text-good">{count(run.match_count)}</span>{" "}
              matched
            </span>
            <span>
              <span className="num text-warn">
                {count(run.exception_count)}
              </span>{" "}
              exceptions worth{" "}
              <span className="num text-warn">
                {rupeesShort(run.exception_value_rupees)}
              </span>
            </span>
            <span>
              <span className="num text-bright">
                {run.duration_seconds.toFixed(3)}s
              </span>
            </span>
            {run.llm_calls > 0 && (
              <span>
                <span className="num text-accent">{run.llm_calls}</span>{" "}
                adjudicator calls
              </span>
            )}
          </div>
        )}
      </header>

      <main className="mx-auto max-w-[1600px] px-5 py-5">
        {error && (
          <div className="mb-4 rounded border border-bad/30 bg-bad/10 px-4 py-3 text-sm text-bad">
            {error}
          </div>
        )}

        {!run ? (
          <div className="rounded-lg border border-line bg-ink-800 px-6 py-16 text-center">
            <h1 className="text-lg font-semibold">No run yet</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-mute">
              Pick a batch and reconcile it. Kosh reads the ERP ledger, the
              gateway exports and the bank statement, then reports what it
              matched, what it refused to match, and why.
            </p>
          </div>
        ) : (
          <>
            <nav className="mb-4 flex gap-1 border-b border-line">
              {TABS.map((option) => (
                <button
                  key={option.id}
                  onClick={() => setTab(option.id)}
                  className={`-mb-px border-b-2 px-3.5 py-2 text-sm transition ${
                    tab === option.id
                      ? "border-accent text-bright"
                      : "border-transparent text-mute hover:text-bright"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </nav>

            {tab === "scorecard" &&
              (card ? (
                <ScorecardView card={card} />
              ) : (
                <div className="rounded-lg border border-line bg-ink-800 px-6 py-12 text-center text-sm text-mute">
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
  );
}
