/**
 * Every match the engine made, with the rule that made it.
 *
 * The reason column is the widest one on purpose. A match nobody can read is
 * indistinguishable from a guess, and this screen exists to make that
 * impossible to hide.
 */

import { useEffect, useMemo, useState } from "react";
import { api, type MatchItem } from "../api";
import { count, legLabel, rupees, tierTone } from "../format";
import { Chip, Empty, Panel, Tag, Td, Th } from "../ui";

const LEGS = [
  "leg1_settlement_to_bank",
  "leg2_payment_to_settlement",
  "leg3_payment_to_invoice",
  "leg4_bank_to_invoice",
];

export function MatchesView({ runId }: { runId: string }) {
  const [items, setItems] = useState<MatchItem[]>([]);
  const [leg, setLeg] = useState("");
  const [tier, setTier] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    void api.matches(runId).then((page) => {
      setItems(page.items);
      setLoading(false);
    });
  }, [runId]);

  const tiers = useMemo(
    () => [...new Set(items.map((i) => i.tier))].sort(),
    [items],
  );

  const shown = useMemo(
    () =>
      items.filter(
        (item) =>
          (!leg || item.leg === leg) && (!tier || item.tier === tier),
      ),
    [items, leg, tier],
  );

  const value = shown.reduce((total, item) => total + item.amount_rupees, 0);

  return (
    <Panel
      title="Matches"
      subtitle={`${count(shown.length)} of ${count(items.length)} · ${rupees(value)} reconciled`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 pb-4">
        <Chip active={!leg} onClick={() => setLeg("")}>
          All legs
        </Chip>
        {LEGS.map((option) => (
          <Chip
            key={option}
            active={leg === option}
            onClick={() => setLeg(leg === option ? "" : option)}
          >
            {legLabel(option)}
          </Chip>
        ))}
        <span className="mx-2 h-4 w-px bg-line" />
        <Chip active={!tier} onClick={() => setTier("")}>
          All tiers
        </Chip>
        {tiers.map((option) => (
          <Chip
            key={option}
            active={tier === option}
            onClick={() => setTier(tier === option ? "" : option)}
          >
            {option}
          </Chip>
        ))}
      </div>

      <div className="max-h-[68vh] overflow-auto">
        {loading ? (
          <Empty>Loading…</Empty>
        ) : shown.length === 0 ? (
          <Empty>No matches under this filter.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                <Th>Tier</Th>
                <Th>Link</Th>
                <Th className="text-right">Amount</Th>
                <Th className="text-right">Conf.</Th>
                <Th>Why</Th>
              </tr>
            </thead>
            <tbody>
              {shown.map((item, index) => (
                <tr
                  key={`${item.leg}-${item.left_id}-${item.right_id}-${index}`}
                  className="hover:bg-card-2/60"
                >
                  <Td>
                    <Tag tone={tierTone(item.tier)}>{item.tier}</Tag>
                  </Td>
                  <Td className="num whitespace-nowrap">
                    <span className="text-mute">{item.left_id}</span>
                    <span className="mx-1.5 text-mute/50">→</span>
                    <span className="text-ink">{item.right_id}</span>
                  </Td>
                  <Td className="num text-right whitespace-nowrap">
                    {rupees(item.amount_rupees)}
                  </Td>
                  <Td
                    className={`num text-right ${item.confidence >= 0.9 ? "text-good" : "text-warn"}`}
                  >
                    {item.confidence.toFixed(2)}
                  </Td>
                  <Td className="max-w-lg text-xs leading-relaxed text-mute">
                    <span className="num mr-1.5 text-brand-ink/80">
                      {item.rule}
                    </span>
                    {item.reason}
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
