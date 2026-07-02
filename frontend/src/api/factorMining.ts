import { authFetch, parseError } from "./client";

export type FactorGrade = "A" | "B" | "C" | "D";

export interface FactorMiningRun {
  id: string;
  source_tag: string;
  status: string;
  universe: string | null;
  benchmark: string | null;
  candidate_count: number;
  error_message: string | null;
  generated_at: string | null;
  source_summary: string | null;
  created_at: string | null;
  updated_at: string | null;
  params: Record<string, unknown> | null;
}

export interface FactorMiningWindowMetrics {
  score: number | null;
  grade: FactorGrade;
  ic_mean: number | null;
  ic_ir: number | null;
  ic_win_rate: number | null;
  monotonicity: number | null;
  sharpe: number | null;
  strategy_sharpe: number | null;
  top_group_sharpe: number | null;
  long_short_sharpe: number | null;
  turnover: number | null;
  cagr: number | null;
  max_drawdown: number | null;
  strategy_max_drawdown: number | null;
  total_return: number | null;
  benchmark_total_return: number | null;
  excess_total_return: number | null;
  flipped: boolean | null;
}

export interface FactorMiningCandidate {
  id: string;
  row_key: string;
  source_id: string;
  source_label: string;
  row_index: number;
  name: string;
  expression: string;
  holding_period: number;
  n_groups: number;
  cost_rate: number;
  neutralize_industry: boolean;
  neutralize_cap: boolean;
  status: string;
  score: number | null;
  grade: FactorGrade;
  latest_score: number | null;
  history_score: number | null;
  latest: FactorMiningWindowMetrics;
  history: FactorMiningWindowMetrics;
  stability_score: number | null;
  market_fit: string | null;
  failure_modes: string | null;
}

export interface FactorMiningRunDetail {
  run: FactorMiningRun;
  candidates: FactorMiningCandidate[];
}

export interface FactorMiningBacktestMetrics {
  total_return: number;
  benchmark_total_return: number;
  excess_total_return: number;
  cagr: number;
  benchmark_cagr: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
}

export interface FactorMiningDailyReturn {
  date: string;
  strategy_return: number;
  benchmark_return: number;
  strategy_cumulative: number;
  benchmark_cumulative: number;
  excess_cumulative: number;
}

export interface FactorMiningPeriodReturn {
  period: string;
  strategy_return: number;
  benchmark_return: number;
  excess_return: number;
}

export interface FactorMiningBacktestItem {
  row_key: string;
  source_id: string;
  source_label: string;
  row_index: number;
  name: string;
  expression: string;
  holding_period: number;
  status: string;
  error_message: string | null;
  metrics: FactorMiningBacktestMetrics | null;
  daily: FactorMiningDailyReturn[];
  monthly: FactorMiningPeriodReturn[];
  yearly: FactorMiningPeriodReturn[];
}

export interface FactorMiningBacktestSeries {
  run_id: string;
  items: Record<string, FactorMiningBacktestItem>;
  errors: { row_key: string; name: string; message: string }[];
  params: Record<string, unknown>;
}

export interface FactorMiningRefreshResponse {
  run_id: string;
  row_key: string;
  item: FactorMiningBacktestItem;
  errors: { row_key: string; name: string; message: string }[];
  result: Record<string, number>;
}

export interface WQResearchCandidate {
  task_id: string;
  task_type: string;
  combo_key?: string;
  status: string;
  status_label: string;
  decision: string;
  expression: string;
  alpha_id: string | null;
  tag: string | null;
  region: string;
  universe: string;
  delay: number | null;
  decay: number | null;
  neutralization: string;
  truncation: number | null;
  fitness: number | null;
  sharpe: number | null;
  returns: number | null;
  turnover: number | null;
  submitted: boolean;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface WQSubmittedAlpha {
  alpha_id: string;
  expression: string;
  tag: string | null;
  region: string;
  universe: string;
  delay: number;
  neutralization: string;
  sharpe: number | null;
  fitness: number | null;
  returns: number | null;
  turnover: number | null;
  status: string;
  status_label: string;
  submitted_at: string | null;
}

export interface WQResearchBoard {
  configured: Record<string, boolean>;
  policy: Record<string, unknown>;
  thresholds: Record<string, number>;
  summary: {
    candidate_count: number;
    ready_to_submit: number;
    near_ready: number;
    submitted_count: number;
    active_count: number;
    failed_count: number;
  };
  candidates: WQResearchCandidate[];
  submitted_alphas: WQSubmittedAlpha[];
  submit_tasks: Record<string, unknown>[];
}

export async function listFactorMiningRuns(): Promise<{ runs: FactorMiningRun[] }> {
  const res = await authFetch("/api/v1/factor-mining/runs");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getFactorMiningRun(runId: string): Promise<FactorMiningRunDetail> {
  const res = await authFetch(`/api/v1/factor-mining/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getFactorMiningBacktestSeries(runId: string): Promise<FactorMiningBacktestSeries> {
  const res = await authFetch(`/api/v1/factor-mining/runs/${encodeURIComponent(runId)}/backtest-series`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function refreshFactorMiningBacktestSeries(runId: string, rowKey: string): Promise<FactorMiningRefreshResponse> {
  const res = await authFetch(`/api/v1/factor-mining/runs/${encodeURIComponent(runId)}/backtest-series/refresh`, {
    method: "POST",
    body: JSON.stringify({ row_key: rowKey }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getWqResearchBoard(): Promise<WQResearchBoard> {
  const res = await authFetch("/api/v1/wq-brain/research-board");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
