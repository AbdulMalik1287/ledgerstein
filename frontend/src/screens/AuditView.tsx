/**
 * The append-only trail.
 *
 * One line per decision, including the decisions to give up and the decisions
 * a human made. Searchable by subject, because the question a controller
 * actually asks is "why did this row end up like that".
 */

import { useEffect, useMemo, useState } from "react";
import { api, type AuditItem } from "../api";
import { count } from "../format";
import { Empty, Panel, Tag, Td, Th } from "../ui";

const ACTION_TONE: Record<string, string> = {
  match: "text-good border-good/30 bg-good/10",
  flag: "text-warn border-warn/30 bg-warn/10",
  reject: "text-bad border-bad/30 bg-bad/10",
  decline: "text-bad border-bad/30 bg-bad/10",
  error: "text-bad border-bad/30 bg-bad/10",
  resolve: "text-accent border-accent/30 bg-accent/10",
};

export function AuditView({ runId }: { runId: string }) {
  const [items, setItems] = useState<AuditItem[]>([]);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    void api.audit(runId).then((page) => {
      setItems(page.items);
      setLoading(false);
    });
  }, [runId]);

  const actions = useMemo(
    () => [...new Set(items.map((i) => i.action))].sort(),
    [items],
  );

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter(
      (item) =>
        (!action || item.action === action) &&
        (!needle ||
          item.subject.toLowerCase().includes(needle) ||
          item.actor.toLowerCase().includes(needle) ||
          item.detail.toLowerCase().includes(needle)),
    );
  }, [items, search, action]);

  return (
    <Panel
      title="Audit trail"
      subtitle={`${count(shown.length)} of ${count(items.length)} events, in the order they happened`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search a payment id, an invoice, a rule…"
          className="num min-w-64 flex-1 rounded border border-line bg-ink-900 px-3 py-1.5 text-xs text-bright outline-none placeholder:font-sans placeholder:text-mute/60 focus:border-accent"
        />
        {actions.map((option) => (
          <button
            key={option}
            onClick={() => setAction(action === option ? "" : option)}
            className={`rounded border px-2 py-1 text-[11px] transition ${
              action === option
                ? "border-accent bg-accent/15 text-accent"
                : "border-line bg-ink-700 text-mute hover:text-bright"
            }`}
          >
            {option}
          </button>
        ))}
      </div>

      <div className="max-h-[70vh] overflow-auto">
        {loading ? (
          <Empty>Loading…</Empty>
        ) : shown.length === 0 ? (
          <Empty>Nothing matches that search.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                <Th className="text-right">#</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Subject</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {shown.map((item) => (
                <tr key={item.sequence} className="hover:bg-ink-700/50">
                  <Td className="num text-right text-mute/60">
                    {item.sequence}
                  </Td>
                  <Td className="num whitespace-nowrap text-mute">
                    {item.actor}
                  </Td>
                  <Td>
                    <Tag tone={ACTION_TONE[item.action] ?? ""}>
                      {item.action}
                    </Tag>
                  </Td>
                  <Td className="num whitespace-nowrap">{item.subject}</Td>
                  <Td className="max-w-xl text-xs leading-relaxed text-mute">
                    {item.detail}
                    {item.confidence > 0 && (
                      <span className="num ml-1.5 text-mute/60">
                        ({item.confidence.toFixed(2)})
                      </span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
