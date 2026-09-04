/**
 * The honest-numbers screen.
 *
 * Precision sits to the left of recall, and the rupee cost of false matches
 * gets its own tile, because those are the figures a reconciliation demo
 * usually leaves out.
 */

import type { Scorecard } from "../api";
import {
  count,
  legLabel,
  percent,
  rupees,
  rupeesShort,
  wholePercentShares,
} from "../format";
import { Meter, Panel, Stat, Tag, Td, Th } from "../ui";
import { tierTone } from "../format";

export function ScorecardView({ card }: { card: Scorecard }) {
  const { overall } = card;
  const clean = overall.wrong === 0;

  const tiers = Object.entries(card.tier_scores);
  const shares = wholePercentShares(tiers.map(([, stats]) => stats.predicted));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-5">
        <Stat
          label="Precision"
          value={percent(overall.precision)}
          hint={`${count(overall.correct)} of ${count(overall.predicted)} matches correct`}
          tone={overall.precision === 1 ? "text-good" : "text-warn"}
        />
        <Stat
          label="Recall"
          value={percent(overall.recall)}
          hint={`${count(overall.missed)} true links not found`}
          tone={overall.recall > 0.95 ? "text-good" : "text-warn"}
        />
        <Stat
          label="Cost of being wrong"
          value={clean ? "₹0" : rupeesShort(overall.wrong_value_rupees)}
          hint={`${count(overall.wrong)} false matches`}
          tone={clean ? "text-good" : "text-bad"}
        />
        <Stat
          label="In the queue"
          value={rupeesShort(card.exceptions.value_rupees)}
          hint={`${count(card.exceptions.total)} rows for a human`}
          tone="text-warn"
        />
        <Stat
          label="Throughput"
          value={`${count(Math.round(card.throughput_rows_per_second))}/s`}
          hint={`${count(card.rows)} rows in ${card.duration_seconds.toFixed(3)}s`}
        />
      </div>

      <div className="grid gap-4">
        <Panel
          title="Per leg"
          subtitle="Scored against the batch's ground truth, leg by leg"
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <Th>Leg</Th>
                  <Th className="text-right">True</Th>
                  <Th>Precision</Th>
                  <Th>Recall</Th>
                  <Th className="text-right">Wrong</Th>
                  <Th className="text-right">Missed</Th>
                </tr>
              </thead>
              <tbody>
                {card.legs.map((leg) => (
                  <tr key={leg.leg} className="hover:bg-raised">
                    <Td className="whitespace-nowrap">{legLabel(leg.leg)}</Td>
                    <Td className="num text-right text-ink-2">
                      {count(leg.true_links)}
                    </Td>
                    <Td>
                      <Meter
                        value={leg.precision}
                        tone={leg.precision === 1 ? "bg-good" : "bg-warn"}
                      />
                    </Td>
                    <Td>
                      <Meter
                        value={leg.recall}
                        tone={leg.recall > 0.95 ? "bg-good" : "bg-warn"}
                      />
                    </Td>
                    <Td
                      className={`num text-right ${leg.wrong ? "text-bad" : "text-ink-2"}`}
                    >
                      {leg.wrong}
                    </Td>
                    <Td
                      className={`num text-right ${leg.missed ? "text-warn" : "text-ink-2"}`}
                    >
                      {leg.missed}
                    </Td>
                  </tr>
                ))}
                <tr className="bg-raised font-semibold">
                  <Td>Overall</Td>
                  <Td className="num text-right">{count(overall.true_links)}</Td>
                  <Td>
                    <Meter
                      value={overall.precision}
                      tone={overall.precision === 1 ? "bg-good" : "bg-warn"}
                    />
                  </Td>
                  <Td>
                    <Meter
                      value={overall.recall}
                      tone={overall.recall > 0.95 ? "bg-good" : "bg-warn"}
                    />
                  </Td>
                  <Td
                    className={`num text-right ${overall.wrong ? "text-bad" : "text-good"}`}
                  >
                    {overall.wrong}
                  </Td>
                  <Td className="num text-right text-warn">{overall.missed}</Td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Which tier earned it"
          subtitle="A headline match rate hides who did the work"
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <Th>Tier</Th>
                  <Th className="text-right">Matched</Th>
                  <Th className="text-right">Share</Th>
                  <Th className="text-right">Precision</Th>
                  <Th className="text-right">Wrong ₹</Th>
                </tr>
              </thead>
              <tbody>
                {tiers.map(([tier, stats], index) => (
                  <tr key={tier} className="hover:bg-raised">
                    <Td>
                      <Tag tone={tierTone(tier)}>{tier}</Tag>
                    </Td>
                    <Td className="num text-right">{count(stats.predicted)}</Td>
                    <Td className="num text-right text-ink-2">
                      {shares[index]}%
                    </Td>
                    <Td
                      className={`num text-right ${stats.precision === 1 ? "text-good" : "text-warn"}`}
                    >
                      {percent(stats.precision)}
                    </Td>
                    <Td
                      className={`num text-right ${stats.wrong_value_rupees ? "text-bad" : "text-ink-2"}`}
                    >
                      {stats.wrong_value_rupees
                        ? rupeesShort(stats.wrong_value_rupees)
                        : "₹0"}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="border-t border-line bg-raised px-5 py-4 text-[12.5px] leading-relaxed text-ink-2">
            {card.llm_calls > 0 ? (
              <>
                The adjudicator was consulted {card.llm_calls} times on rows the
                rules declined. Its answers were checked against a candidate
                whitelist before being believed.
              </>
            ) : (
              <>
                No adjudicator calls: every match above came from deterministic
                rules. The T4 tier only sees rows the rules refused to decide.
              </>
            )}
          </div>
        </Panel>

      <Panel title="How the queue is graded">
        <div className="divide-y divide-line-soft">
          <div className="px-5 py-4">
            <div className="num text-xl font-semibold text-good">
              {count(card.exceptions.justified)}
            </div>
            <div className="mt-1 text-sm text-ink">Correctly declined</div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Rows that genuinely have no partner. Refusing to match these is
              the right answer, not a failure.
            </p>
          </div>
          <div className="px-5 py-4">
            <div
              className={`num text-xl font-semibold ${card.exceptions.missed_a_real_link ? "text-warn" : "text-good"}`}
            >
              {count(card.exceptions.missed_a_real_link)}
            </div>
            <div className="mt-1 text-sm text-ink">Missed a real link</div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Rows the engine gave up on that did have a correct answer. These
              are the real misses.
            </p>
          </div>
          <div className="px-5 py-4">
            <div className="num text-xl font-semibold text-warn">
              {rupees(card.exceptions.value_rupees)}
            </div>
            <div className="mt-1 text-sm text-ink">Exposure in the queue</div>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              What a controller is being asked to look at, worst row first.
            </p>
          </div>
        </div>
      </Panel>
      </div>
    </div>
  );
}
