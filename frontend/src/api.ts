/**
 * Typed client for the LedgerStein API.
 *
 * Every shape here mirrors what the backend actually returns. Nothing is
 * invented on this side -- if a number appears on screen, the engine computed
 * it, so the dashboard cannot flatter the result by accident.
 */

export type RunSummary = {
  id: string;
  batch: string;
  started_at: string;
  duration_seconds: number;
  rows: number;
  match_count: number;
  exception_count: number;
  exception_value_rupees: number;
  llm_calls: number;
  has_scorecard: boolean;
};

export type LegScore = {
  leg: string;
  true_links: number;
  predicted: number;
  correct: number;
  wrong: number;
  missed: number;
  precision: number;
  recall: number;
  f1: number;
  wrong_value_rupees: number;
  missed_value_rupees: number;
  wrong_examples: string[];
};

export type TierScore = {
  predicted: number;
  correct: number;
  wrong: number;
  precision: number;
  wrong_value_rupees: number;
};

export type Scorecard = {
  batch: string;
  rows: number;
  duration_seconds: number;
  throughput_rows_per_second: number;
  llm_calls: number;
  overall: LegScore;
  legs: LegScore[];
  tier_mix: Record<string, number>;
  tier_scores: Record<string, TierScore>;
  exceptions: {
    total: number;
    justified: number;
    missed_a_real_link: number;
    value_rupees: number;
    by_type: Record<string, number>;
  };
};

export type MatchItem = {
  leg: string;
  left_id: string;
  right_id: string;
  tier: string;
  rule: string;
  reason: string;
  confidence: number;
  amount_rupees: number;
};

export type ExceptionItem = {
  id: number;
  entity_type: string;
  entity_id: string;
  exception_type: string;
  reason: string;
  amount_rupees: number;
  leg: string;
  candidates: string[];
  status: string;
  resolution: string;
  resolved_by: string;
  resolved_at: string | null;
};

export type AuditItem = {
  sequence: number;
  at: string;
  actor: string;
  action: string;
  leg: string;
  subject: string;
  detail: string;
  confidence: number;
};

export type Page<T> = { total: number; items: T[] };

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

const query = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
};

export const api = {
  batches: () => json<{ name: string; has_truth: boolean }[]>("/api/batches"),

  runs: () => json<RunSummary[]>("/api/runs"),

  createRun: (batch: string, useLlm: boolean) =>
    json<RunSummary>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ batch, use_llm: useLlm }),
    }),

  run: (id: string) =>
    json<{ summary: RunSummary; scorecard: Scorecard | null }>(
      `/api/runs/${id}`,
    ),

  matches: (id: string, filters: { leg?: string; tier?: string } = {}) =>
    json<Page<MatchItem>>(
      `/api/runs/${id}/matches${query({ ...filters, limit: 2000 })}`,
    ),

  exceptions: (
    id: string,
    filters: { exception_type?: string; status?: string } = {},
  ) =>
    json<Page<ExceptionItem>>(
      `/api/runs/${id}/exceptions${query({ ...filters, limit: 2000 })}`,
    ),

  exceptionSummary: (id: string) =>
    json<{ exception_type: string; count: number; value_rupees: number }[]>(
      `/api/runs/${id}/exception-summary`,
    ),

  resolve: (exceptionId: number, resolution: string, linkTo: string) =>
    json<ExceptionItem>(`/api/exceptions/${exceptionId}/resolve`, {
      method: "POST",
      body: JSON.stringify({
        resolution,
        resolved_by: "controller",
        link_to: linkTo,
      }),
    }),

  audit: (
    id: string,
    filters: { subject?: string; actor?: string; action?: string } = {},
  ) =>
    json<Page<AuditItem>>(
      `/api/runs/${id}/audit${query({ ...filters, limit: 5000 })}`,
    ),
};
