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
import { Empty, Panel, SkeletonRows, Tag, Td, Th } from "../ui";

const ACTION_TONE: Record<string, string> = {
  match: "bg-good-bg text-good",
  flag: "bg-warn-bg text-warn",
  reject: "bg-bad-bg text-bad",
  decline: "bg-bad-bg text-bad",
  error: "bg-bad-bg text-bad",
  resolve: "bg-accent-soft text-ink",
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
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 pb-4">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search a payment id, an invoice, a rule…"
          className="num min-w-64 flex-1 rounded-full border border-line bg-raised px-4 py-2 text-[12.5px] text-ink outline-none placeholder:font-sans placeholder:text-ink-2 focus:border-accent focus:bg-surface"
        />
        {actions.map((option) => (
          <button
            key={option}
            onClick={() => setAction(action === option ? "" : option)}
            className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
              action === option
                ? "border-accent bg-accent-soft font-medium text-ink"
                : "border-line bg-surface text-ink-2 hover:bg-raised"
            }`}
          >
            {option}
          </button>
        ))}
      </div>

      <div className="max-h-[70vh] overflow-auto">
        {loading ? (
          <SkeletonRows rows={9} />
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
                <tr key={item.sequence} className="hover:bg-raised">
                  <Td className="num text-right text-faint">
                    {item.sequence}
                  </Td>
                  <Td className="num whitespace-nowrap text-ink-2">
                    {item.actor}
                  </Td>
                  <Td>
                    <Tag tone={ACTION_TONE[item.action] ?? ""}>
                      {item.action}
                    </Tag>
                  </Td>
                  <Td className="num whitespace-nowrap">{item.subject}</Td>
                  <Td className="max-w-xl text-xs leading-relaxed text-ink-2">
                    {item.detail}
                    {item.confidence > 0 && (
                      <span className="num ml-1.5 text-faint">
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
