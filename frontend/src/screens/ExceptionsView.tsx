/**
 * The exception queue -- the screen this project is really about.
 *
 * Sorted by rupee exposure rather than insertion order, because a queue that
 * buries the one row that matters under forty small ones is a queue nobody
 * works. Each row carries the engine's own diagnosis, and resolving one writes
 * into the same audit trail as the engine's decisions.
 */

import { useEffect, useMemo, useState } from "react";
import { api, type ExceptionItem } from "../api";
import { count, exceptionTone, legLabel, rupees, rupeesShort } from "../format";
import { Empty, Panel, Tag, Td, Th } from "../ui";

type Summary = { exception_type: string; count: number; value_rupees: number };

export function ExceptionsView({ runId }: { runId: string }) {
  const [summary, setSummary] = useState<Summary[]>([]);
  const [items, setItems] = useState<ExceptionItem[]>([]);
  const [active, setActive] = useState<string>("");
  const [selected, setSelected] = useState<ExceptionItem | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const [rows, groups] = await Promise.all([
      api.exceptions(runId),
      api.exceptionSummary(runId),
    ]);
    setItems(rows.items);
    setSummary(groups);
    setLoading(false);
  };

  useEffect(() => {
    void load();
    setSelected(null);
    setActive("");
  }, [runId]);

  const shown = useMemo(
    () => (active ? items.filter((i) => i.exception_type === active) : items),
    [items, active],
  );

  const openValue = shown
    .filter((i) => i.status === "open")
    .reduce((total, i) => total + i.amount_rupees, 0);

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-2">
        <Panel
          title="Exception queue"
          subtitle={`${count(shown.length)} rows · ${rupees(openValue)} still open`}
          right={
            active ? (
              <button
                onClick={() => setActive("")}
                className="rounded border border-line bg-ink-600 px-2 py-1 text-xs text-mute transition hover:text-bright"
              >
                Clear filter
              </button>
            ) : null
          }
        >
          <div className="flex flex-wrap gap-1.5 border-b border-line px-4 py-3">
            {summary.map((group) => (
              <button
                key={group.exception_type}
                onClick={() =>
                  setActive(
                    active === group.exception_type ? "" : group.exception_type,
                  )
                }
                className={`rounded border px-2 py-1 text-[11px] transition ${
                  active === group.exception_type
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-line bg-ink-700 text-mute hover:border-line hover:text-bright"
                }`}
              >
                {group.exception_type}
                <span className="num ml-1.5 text-bright">{group.count}</span>
                <span className="num ml-1.5 opacity-60">
                  {rupeesShort(group.value_rupees)}
                </span>
              </button>
            ))}
          </div>

          <div className="max-h-[62vh] overflow-auto">
            {loading ? (
              <Empty>Loading…</Empty>
            ) : shown.length === 0 ? (
              <Empty>Nothing in this bucket.</Empty>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <Th>Type</Th>
                    <Th>Entity</Th>
                    <Th className="text-right">Amount</Th>
                    <Th>Diagnosis</Th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((item) => (
                    <tr
                      key={item.id}
                      onClick={() => setSelected(item)}
                      className={`cursor-pointer transition hover:bg-ink-700/60 ${
                        selected?.id === item.id ? "bg-ink-700" : ""
                      } ${item.status === "resolved" ? "opacity-45" : ""}`}
                    >
                      <Td>
                        <Tag tone={exceptionTone(item.exception_type)}>
                          {item.exception_type}
                        </Tag>
                      </Td>
                      <Td className="num whitespace-nowrap text-mute">
                        {item.entity_id}
                      </Td>
                      <Td className="num text-right whitespace-nowrap">
                        {rupees(item.amount_rupees)}
                      </Td>
                      <Td className="max-w-md text-xs leading-relaxed text-mute">
                        {item.reason}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Panel>
      </div>

      <ExceptionDetail
        item={selected}
        onResolved={(updated) => {
          setItems((rows) =>
            rows.map((r) => (r.id === updated.id ? updated : r)),
          );
          setSelected(updated);
        }}
      />
    </div>
  );
}

function ExceptionDetail({
  item,
  onResolved,
}: {
  item: ExceptionItem | null;
  onResolved: (item: ExceptionItem) => void;
}) {
  const [resolution, setResolution] = useState("");
  const [linkTo, setLinkTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setResolution("");
    setLinkTo(item?.candidates[0] ?? "");
    setError("");
  }, [item?.id]);

  if (!item) {
    return (
      <Panel title="Detail">
        <Empty>Select a row to see the evidence behind it.</Empty>
      </Panel>
    );
  }

  const submit = async () => {
    if (!resolution.trim()) {
      setError("Say what you decided. The trail is only useful if it explains.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onResolved(await api.resolve(item.id, resolution.trim(), linkTo));
    } catch (problem) {
      setError(String(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title={item.entity_id}
      subtitle={`${item.entity_type} · ${legLabel(item.leg)}`}
      right={
        <Tag tone={exceptionTone(item.exception_type)}>
          {item.exception_type}
        </Tag>
      }
    >
      <div className="space-y-4 px-4 py-4">
        <div>
          <div className="num text-2xl font-semibold">
            {rupees(item.amount_rupees)}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-mute">{item.reason}</p>
        </div>

        {item.candidates.length > 0 && (
          <div>
            <div className="text-[11px] font-semibold tracking-wide text-mute uppercase">
              Candidates considered
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {item.candidates.map((candidate) => (
                <button
                  key={candidate}
                  onClick={() => setLinkTo(candidate)}
                  disabled={item.status === "resolved"}
                  className={`num rounded border px-2 py-1 text-xs transition disabled:cursor-default ${
                    linkTo === candidate
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-line bg-ink-700 text-mute hover:text-bright"
                  }`}
                >
                  {candidate}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-mute">
              The engine declined rather than picking one of these. Nothing in
              the exports separates them.
            </p>
          </div>
        )}

        {item.status === "resolved" ? (
          <div className="rounded border border-good/30 bg-good/10 px-3 py-3">
            <div className="text-xs font-semibold tracking-wide text-good uppercase">
              Resolved
            </div>
            <p className="mt-1.5 text-sm text-bright">{item.resolution}</p>
            <p className="num mt-1.5 text-xs text-mute">
              {item.resolved_by} · {item.resolved_at}
            </p>
          </div>
        ) : (
          <div className="space-y-2 border-t border-line pt-4">
            <label
              htmlFor="resolution"
              className="block text-[11px] font-semibold tracking-wide text-mute uppercase"
            >
              Resolve by hand
            </label>
            <textarea
              id="resolution"
              rows={3}
              value={resolution}
              onChange={(event) => setResolution(event.target.value)}
              placeholder="What did you find out, and how?"
              className="w-full rounded border border-line bg-ink-900 px-3 py-2 text-sm text-bright outline-none placeholder:text-mute/60 focus:border-accent"
            />
            {error && <p className="text-xs text-bad">{error}</p>}
            <button
              onClick={submit}
              disabled={busy}
              className="w-full rounded bg-accent px-3 py-2 text-sm font-semibold text-ink-900 transition hover:brightness-110 disabled:opacity-50"
            >
              {busy ? "Recording…" : "Record decision"}
            </button>
            <p className="text-xs leading-relaxed text-mute">
              This is written to the audit trail as{" "}
              <span className="num">human:controller</span>, alongside the
              engine's own decisions.
            </p>
          </div>
        )}
      </div>
    </Panel>
  );
}
