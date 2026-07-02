import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import * as echarts from "echarts";
import { Brain, ExternalLink, Loader2, RefreshCw, Search, Sparkles } from "lucide-react";
import {
  getFactorMiningBacktestSeries,
  getFactorMiningRun,
  getWqResearchBoard,
  listFactorMiningRuns,
  refreshFactorMiningBacktestSeries,
  type FactorGrade,
  type FactorMiningDailyReturn,
  type FactorMiningBacktestItem,
  type FactorMiningBacktestSeries,
  type FactorMiningCandidate,
  type FactorMiningPeriodReturn,
  type FactorMiningRun,
  type FactorMiningRunDetail,
  type WQResearchBoard,
} from "../api/factorMining";
import { useColorMode } from "../contexts/ColorModeContext";

type ViewName = "a-share" | "wq";
type SortKey = "score" | "ic_ir" | "strategy_sharpe" | "max_drawdown" | "turnover";
type ReturnGranularity = "daily" | "monthly" | "yearly";
type DetailSortKey =
  | "grade"
  | "name"
  | "expression"
  | "holdingPeriod"
  | "score"
  | "latestScore"
  | "historyScore"
  | "ic"
  | "icir"
  | "winRate"
  | "monotonicity"
  | "sharpe"
  | "longShortSharpe"
  | "turnover"
  | "cagr"
  | "maxDrawdown"
  | "flipped";
type SortDirection = "asc" | "desc";
type DailyReturnKey = "strategy_return" | "benchmark_return" | "strategy_cumulative" | "benchmark_cumulative" | "excess_cumulative";
type PeriodReturnKey = "strategy_return" | "benchmark_return" | "excess_return";

interface ScatterPoint {
  row_key: string;
  name: string;
  score: number;
  ic_ir: number;
  ic_mean: number;
  strategy_sharpe: number;
  cagr: number;
  max_drawdown: number;
  turnover: number;
  grade: FactorGrade;
  expression: string;
}

interface EChartsClickPayload {
  data?: unknown;
  dataIndex?: number;
  seriesName?: string;
  value?: unknown;
}

interface ScatterChartPoint extends ScatterPoint {
  value: [number, number];
  ic_mean: number;
  strategy_sharpe: number;
  cagr: number;
  max_drawdown: number;
  turnover: number;
  symbolSize: number;
}

interface BarChartPoint {
  value: number;
  row_key: string;
}

interface TooltipParam {
  data?: unknown;
  dataIndex?: number;
  seriesName?: string;
  marker?: string;
}

interface BacktestSeriesDefinition {
  key: DailyReturnKey | PeriodReturnKey;
  name: string;
  color: string;
  description: string;
  lineType: "solid" | "dashed";
}

const GRADE_RANK: Record<FactorGrade, number> = { A: 4, B: 3, C: 2, D: 1 };
const GRADE_COLORS: Record<FactorGrade, string> = {
  A: "#2563eb",
  B: "#7c3aed",
  C: "#d97706",
  D: "#ea580c",
};
const BACKTEST_PALETTE = {
  strategy: "#2563eb",
  benchmark: "#64748b",
  excess: "#d97706",
};
function asNumber(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return value;
}

function fmtNumber(value: number | null | undefined, digits: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function fmtPercent(value: number | null | undefined, digits: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

function fmtDate(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function valueColorClass(value: number | null | undefined, isDark: boolean): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value === 0) {
    return isDark ? "text-gray-300" : "text-gray-700";
  }
  return value > 0
    ? isDark ? "text-emerald-300" : "text-emerald-700"
    : isDark ? "text-red-300" : "text-red-700";
}

function isTooltipParamArray(value: unknown): value is TooltipParam[] {
  return Array.isArray(value);
}

function isScatterChartPoint(value: unknown): value is ScatterChartPoint {
  if (typeof value !== "object" || value === null) return false;
  return "row_key" in value && "value" in value;
}

function isBarChartPoint(value: unknown): value is BarChartPoint {
  if (typeof value !== "object" || value === null) return false;
  return "row_key" in value && "value" in value;
}

function tooltipBlock(seriesName: string, color: string, valueLabel: string, description: string): string {
  return [
    `<div style="border-left:4px solid ${escapeHtml(color)};padding-left:10px;margin-top:8px;">`,
    `<div style="font-weight:700;color:${escapeHtml(color)};">${escapeHtml(seriesName)}</div>`,
    `<div style="margin-top:4px;">当前值：${escapeHtml(valueLabel)}</div>`,
    `<div style="margin-top:4px;color:#64748b;white-space:normal;">${escapeHtml(description)}</div>`,
    "</div>",
  ].join("");
}

function metricValue(row: FactorMiningCandidate, key: DetailSortKey): number | string | boolean | null {
  if (key === "grade") return GRADE_RANK[row.grade] ?? 0;
  if (key === "name") return row.name;
  if (key === "expression") return row.expression;
  if (key === "holdingPeriod") return row.holding_period;
  if (key === "score") return row.score;
  if (key === "latestScore") return row.latest_score;
  if (key === "historyScore") return row.history_score;
  if (key === "ic") return row.latest.ic_mean;
  if (key === "icir") return row.latest.ic_ir;
  if (key === "winRate") return row.latest.ic_win_rate;
  if (key === "monotonicity") return row.latest.monotonicity;
  if (key === "sharpe") return row.latest.sharpe ?? row.latest.strategy_sharpe;
  if (key === "longShortSharpe") return row.latest.long_short_sharpe;
  if (key === "turnover") return row.latest.turnover;
  if (key === "cagr") return row.latest.cagr;
  if (key === "maxDrawdown") return row.latest.max_drawdown;
  return row.latest.flipped === true;
}

function compareValues(leftValue: number | string | boolean | null, rightValue: number | string | boolean | null): number {
  const leftMissing = leftValue === null || leftValue === undefined || leftValue === "";
  const rightMissing = rightValue === null || rightValue === undefined || rightValue === "";
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  if (typeof leftValue === "string" || typeof rightValue === "string") {
    return String(leftValue).localeCompare(String(rightValue), "zh-CN", { numeric: true });
  }
  if (typeof leftValue === "boolean" || typeof rightValue === "boolean") {
    return Number(leftValue) - Number(rightValue);
  }
  return asNumber(leftValue) - asNumber(rightValue);
}

function sortRowsByDetail(rows: FactorMiningCandidate[], key: DetailSortKey, direction: SortDirection): FactorMiningCandidate[] {
  const multiplier = direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const compared = compareValues(metricValue(left, key), metricValue(right, key));
    if (compared !== 0) return compared * multiplier;
    return left.row_index - right.row_index;
  });
}

function sortValue(candidate: FactorMiningCandidate, sortKey: SortKey): number {
  if (sortKey === "score") return asNumber(candidate.score);
  if (sortKey === "ic_ir") return asNumber(candidate.latest.ic_ir);
  if (sortKey === "strategy_sharpe") return asNumber(candidate.latest.sharpe ?? candidate.latest.strategy_sharpe);
  if (sortKey === "max_drawdown") return -Math.abs(asNumber(candidate.latest.max_drawdown));
  return -asNumber(candidate.latest.turnover);
}

function runLabel(run: FactorMiningRun): string {
  const universe = run.universe ?? "未知股票池";
  const count = `${run.candidate_count} 个因子`;
  const time = run.generated_at ? new Date(run.generated_at).toLocaleDateString("zh-CN") : "无日期";
  return `${universe} · ${count} · ${time}`;
}

function buildAskGptPrompt(row: FactorMiningCandidate): string {
  return [
    "请用中文解释这个A股量化因子的设计思路。",
    "",
    `因子名称：${row.name || "-"}`,
    `因子表达式：${row.expression || "-"}`,
    `持仓周期：${row.holding_period} 个交易日`,
    `综合评分 Score：${fmtNumber(row.score, 1)}`,
    `等级：${row.grade || "-"}`,
    `IC：${fmtNumber(row.latest.ic_mean, 4)}`,
    `ICIR：${fmtNumber(row.latest.ic_ir, 3)}`,
    `IC 胜率：${fmtPercent(row.latest.ic_win_rate, 1)}`,
    `单调性：${fmtNumber(row.latest.monotonicity, 2)}`,
    `Sharpe：${fmtNumber(row.latest.sharpe ?? row.latest.strategy_sharpe, 3)}`,
    `多空 Sharpe：${fmtNumber(row.latest.long_short_sharpe, 3)}`,
    `换手率：${fmtPercent(row.latest.turnover, 2)}`,
    `CAGR：${fmtPercent(row.latest.cagr, 2)}`,
    `MaxDD：${fmtPercent(row.latest.max_drawdown, 2)}`,
    `是否自动翻转：${row.latest.flipped === true ? "是" : "否"}`,
    "",
    "请按下面结构回答：",
    "1. 这个因子想捕捉什么市场行为或交易逻辑。",
    "2. 表达式中每个组成部分分别是什么意思。",
    "3. 为什么它可能在对应股票池里有效。",
    "4. 从这些指标看，它的强项和弱点是什么。",
    "5. 可能失效的市场环境和主要风险。",
    "6. 下一步应该如何验证、改造或优化。",
    "",
    "请避免泛泛而谈，也不要把它当成投资建议。",
  ].join("\n");
}

function openAskGpt(row: FactorMiningCandidate): void {
  const url = `https://chat.openai.com/?hints=search&q=${encodeURIComponent(buildAskGptPrompt(row))}`;
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (opened) opened.opener = null;
}

function gradeClass(grade: FactorGrade, isDark: boolean): string {
  if (grade === "A") return isDark ? "bg-blue-500/15 text-blue-300 border-blue-500/30" : "bg-blue-50 text-blue-700 border-blue-200";
  if (grade === "B") return isDark ? "bg-violet-500/15 text-violet-300 border-violet-500/30" : "bg-violet-50 text-violet-700 border-violet-200";
  if (grade === "C") return isDark ? "bg-amber-500/15 text-amber-300 border-amber-500/30" : "bg-amber-50 text-amber-700 border-amber-200";
  return isDark ? "bg-orange-500/15 text-orange-300 border-orange-500/30" : "bg-orange-50 text-orange-700 border-orange-200";
}

function scatterTooltip(param: unknown): string {
  const item = param as TooltipParam;
  if (!isScatterChartPoint(item.data)) return "";
  const row = item.data;
  return [
    `<div style="max-width:340px;">`,
    `<div style="font-weight:700;margin-bottom:6px;">${escapeHtml(row.name || row.row_key)}</div>`,
    `<div>等级：${escapeHtml(row.grade)}　Score：${fmtNumber(row.score, 1)}</div>`,
    `<div>ICIR：${fmtNumber(row.ic_ir, 3)}　IC：${fmtNumber(row.ic_mean, 4)}</div>`,
    `<div>Sharpe：${fmtNumber(row.strategy_sharpe, 3)}　CAGR：${fmtPercent(row.cagr, 2)}</div>`,
    `<div>MaxDD：${fmtPercent(row.max_drawdown, 2)}　换手：${fmtPercent(row.turnover, 2)}</div>`,
    `<div style="margin-top:6px;color:#64748b;white-space:normal;">${escapeHtml(row.expression)}</div>`,
    `</div>`,
  ].join("");
}

function buildScatterSeries(rows: ScatterPoint[], selectedRowKey: string): echarts.SeriesOption[] {
  return (["A", "B", "C", "D"] as FactorGrade[]).map((grade) => ({
    name: `${grade} 级`,
    type: "scatter",
    data: rows.filter((row) => row.grade === grade).map<ScatterChartPoint>((row) => ({
      ...row,
      value: [row.ic_ir, row.score],
      ic_mean: asNumber(row.ic_mean),
      strategy_sharpe: asNumber(row.strategy_sharpe),
      cagr: asNumber(row.cagr),
      max_drawdown: asNumber(row.max_drawdown),
      turnover: asNumber(row.turnover),
      symbolSize: row.row_key === selectedRowKey ? 19 : grade === "A" ? 15 : 12,
    })),
    cursor: "pointer",
    itemStyle: {
      color: GRADE_COLORS[grade],
      opacity: 0.78,
    },
    emphasis: {
      scale: 1.7,
      focus: "self",
      itemStyle: {
        opacity: 1,
        borderColor: "#111827",
        borderWidth: 1.5,
      },
    },
    markLine: grade === "A" ? {
      silent: true,
      symbol: "none",
      lineStyle: {
        type: "dashed",
        width: 1.2,
      },
      data: [
        {
          yAxis: 80,
          lineStyle: { color: GRADE_COLORS.A },
          label: { formatter: "A 级线", color: GRADE_COLORS.A, position: "insideEndTop" },
        },
        {
          xAxis: 0,
          lineStyle: { color: "#d97706" },
          label: { formatter: "ICIR 0", color: "#9a651f", position: "insideEndTop" },
        },
      ],
    } : undefined,
  }));
}

function scatterOption(rows: ScatterPoint[], selectedRowKey: string, isDark: boolean): echarts.EChartsOption {
  const xValues = rows.map((row) => row.ic_ir);
  const minX = Math.min(-0.2, ...xValues);
  const maxX = Math.max(0.2, ...xValues);
  const xPadding = Math.max((maxX - minX) * 0.08, 0.04);
  const axisText = isDark ? "#9ca3af" : "#64748b";
  const splitLine = isDark ? "#1f2937" : "#e5e7eb";
  return {
    animationDuration: 260,
    color: [GRADE_COLORS.A, GRADE_COLORS.B, GRADE_COLORS.C, GRADE_COLORS.D],
    grid: { left: 54, right: 48, top: 42, bottom: 72, containLabel: true },
    legend: {
      top: 0,
      right: 6,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: axisText },
    },
    tooltip: {
      trigger: "item",
      confine: true,
      appendToBody: true,
      formatter: scatterTooltip,
      backgroundColor: isDark ? "rgba(17, 24, 39, 0.96)" : "rgba(255, 255, 255, 0.96)",
      borderColor: isDark ? "#374151" : "#d1d5db",
      borderWidth: 1,
      textStyle: { color: isDark ? "#e5e7eb" : "#111827", fontSize: 12 },
    },
    xAxis: {
      name: "ICIR",
      nameLocation: "middle",
      nameGap: 34,
      min: Math.floor((minX - xPadding) * 100) / 100,
      max: Math.ceil((maxX + xPadding) * 100) / 100,
      splitLine: { lineStyle: { color: splitLine } },
      axisLine: { lineStyle: { color: isDark ? "#374151" : "#cbd5e1" } },
      axisLabel: { color: axisText },
      nameTextStyle: { color: axisText },
    },
    yAxis: {
      name: "Score",
      nameLocation: "middle",
      nameGap: 38,
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: splitLine } },
      axisLine: { lineStyle: { color: isDark ? "#374151" : "#cbd5e1" } },
      axisLabel: { color: axisText },
      nameTextStyle: { color: axisText },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: 0, filterMode: "none" },
      {
        type: "slider",
        xAxisIndex: 0,
        height: 18,
        bottom: 10,
        filterMode: "none",
        borderColor: isDark ? "#374151" : "#d1d5db",
        fillerColor: "rgba(37, 99, 235, 0.16)",
        handleStyle: { color: "#2563eb" },
        textStyle: { color: axisText },
      },
    ],
    series: buildScatterSeries(rows, selectedRowKey),
  };
}

function barOption(rows: FactorMiningCandidate[], isDark: boolean): echarts.EChartsOption {
  const topRows = rows.slice(0, 10);
  const axisText = isDark ? "#9ca3af" : "#64748b";
  const trackColor = isDark ? "#1f2937" : "#eef2ed";
  return {
    animationDuration: 260,
    grid: { left: 178, right: 54, top: 12, bottom: 10, containLabel: false },
    tooltip: {
      trigger: "item",
      confine: true,
      appendToBody: true,
      formatter: (param: unknown) => {
        const item = param as TooltipParam;
        const data = item.data;
        if (!isBarChartPoint(data)) return "";
        const row = topRows.find((candidate) => candidate.row_key === data.row_key);
        if (!row) return "";
        return [
          `<div style="max-width:320px;">`,
          `<div style="font-weight:700;margin-bottom:6px;">${escapeHtml(row.name || row.row_key)}</div>`,
          `<div>等级：${escapeHtml(row.grade)}　Score：${fmtNumber(row.score, 1)}</div>`,
          `<div>ICIR：${fmtNumber(row.latest.ic_ir, 3)}　Sharpe：${fmtNumber(row.latest.sharpe ?? row.latest.strategy_sharpe, 3)}</div>`,
          `<div style="margin-top:6px;color:#64748b;white-space:normal;">${escapeHtml(row.expression)}</div>`,
          `</div>`,
        ].join("");
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: 100,
      show: false,
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: topRows.map((row) => row.name || row.row_key),
      axisLabel: {
        color: axisText,
        width: 166,
        overflow: "truncate",
        fontSize: 12,
      },
      axisTick: { show: false },
      axisLine: { show: false },
    },
    series: [
      {
        type: "bar",
        data: topRows.map<BarChartPoint>((row) => ({ value: asNumber(row.score), row_key: row.row_key })),
        barMaxWidth: 22,
        showBackground: true,
        backgroundStyle: {
          color: trackColor,
          borderRadius: 4,
        },
        label: {
          show: true,
          position: "right",
          formatter: (params: { value?: unknown }) => {
            const value = typeof params.value === "number" ? params.value : Number(params.value);
            return Number.isFinite(value) ? fmtNumber(value, 1) : "-";
          },
          color: axisText,
          fontSize: 12,
        },
        itemStyle: {
          borderRadius: 4,
          color: (params: { dataIndex: number }) => GRADE_COLORS[topRows[params.dataIndex]?.grade ?? "D"],
        },
      },
    ],
  };
}

function backtestDefinitions(granularity: ReturnGranularity): BacktestSeriesDefinition[] {
  if (granularity === "daily") {
    return [
      { key: "strategy_cumulative", name: "策略累计", color: BACKTEST_PALETTE.strategy, description: "因子策略从起始日开始累计到当前日期的收益。", lineType: "solid" },
      { key: "benchmark_cumulative", name: "基准累计", color: BACKTEST_PALETTE.benchmark, description: "同期基准从起始日开始累计到当前日期的收益。", lineType: "solid" },
      { key: "excess_cumulative", name: "超额累计", color: BACKTEST_PALETTE.excess, description: "策略累计收益减去基准累计收益后的超额部分。", lineType: "dashed" },
    ];
  }
  const periodName = granularity === "monthly" ? "月收益" : "年收益";
  return [
    { key: "strategy_return", name: `策略${periodName}`, color: BACKTEST_PALETTE.strategy, description: "当前周期内，因子策略本身的收益。", lineType: "solid" },
    { key: "benchmark_return", name: `基准${periodName}`, color: BACKTEST_PALETTE.benchmark, description: "当前周期内，基准的收益。", lineType: "solid" },
    { key: "excess_return", name: `超额${periodName}`, color: BACKTEST_PALETTE.excess, description: "当前周期内，策略收益减去基准收益后的超额部分。", lineType: "dashed" },
  ];
}

function dailyReturnValue(row: FactorMiningDailyReturn, key: DailyReturnKey | PeriodReturnKey): number {
  if (key === "excess_return") return 0;
  return row[key];
}

function periodReturnValue(row: FactorMiningPeriodReturn, key: DailyReturnKey | PeriodReturnKey): number {
  if (key === "strategy_cumulative" || key === "benchmark_cumulative" || key === "excess_cumulative") return 0;
  return row[key];
}

function dailyBacktestTooltip(params: unknown, rows: FactorMiningDailyReturn[], definitions: BacktestSeriesDefinition[]): string {
  if (!isTooltipParamArray(params)) return "";
  const row = rows[params[0]?.dataIndex ?? -1];
  if (!row) return "";
  const blocks = params.map((item) => {
    const definition = definitions.find((candidate) => candidate.name === item.seriesName);
    if (!definition) return "";
    const value = dailyReturnValue(row, definition.key);
    return tooltipBlock(definition.name, definition.color, fmtPercent(Number(value), 2), definition.description);
  }).join("");
  return [
    `<div style="min-width:260px;">`,
    `<div style="font-weight:700;margin-bottom:6px;">${escapeHtml(row.date)}</div>`,
    `<div>策略当日：${fmtPercent(row.strategy_return, 2)}</div>`,
    `<div>基准当日：${fmtPercent(row.benchmark_return, 2)}</div>`,
    blocks,
    `</div>`,
  ].join("");
}

function periodBacktestTooltip(params: unknown, rows: FactorMiningPeriodReturn[], definitions: BacktestSeriesDefinition[]): string {
  if (!isTooltipParamArray(params)) return "";
  const row = rows[params[0]?.dataIndex ?? -1];
  if (!row) return "";
  const blocks = params.map((item) => {
    const definition = definitions.find((candidate) => candidate.name === item.seriesName);
    if (!definition) return "";
    const value = periodReturnValue(row, definition.key);
    return tooltipBlock(definition.name, definition.color, fmtPercent(Number(value), 2), definition.description);
  }).join("");
  return `<div style="min-width:240px;"><div style="font-weight:700;margin-bottom:6px;">${escapeHtml(row.period)}</div>${blocks}</div>`;
}

function backtestOption(item: FactorMiningBacktestItem, granularity: ReturnGranularity, isDark: boolean): echarts.EChartsOption {
  const axisText = isDark ? "#9ca3af" : "#64748b";
  const splitLine = isDark ? "#1f2937" : "#e5e7eb";
  const definitions = backtestDefinitions(granularity);
  const base = {
    animationDuration: 260,
    color: definitions.map((definition) => definition.color),
    grid: { left: 54, right: 24, top: 30, bottom: granularity === "daily" ? 58 : 48, containLabel: true },
    legend: { top: 0, right: 8, textStyle: { color: axisText } },
    yAxis: {
      type: "value",
      axisLabel: { color: axisText, formatter: (value: number) => `${(value * 100).toFixed(1)}%` },
      splitLine: { lineStyle: { color: splitLine } },
    },
  } satisfies echarts.EChartsOption;
  if (granularity === "daily") {
    const rows = item.daily ?? [];
    return {
      ...base,
      tooltip: { trigger: "axis", confine: true, appendToBody: true, formatter: (params: unknown) => dailyBacktestTooltip(params, rows, definitions) },
      axisPointer: { show: true, type: "line", lineStyle: { color: "#94a3b8", type: "dashed" } },
      xAxis: { type: "category", data: rows.map((row) => row.date), axisLabel: { color: axisText }, axisLine: { lineStyle: { color: isDark ? "#374151" : "#cbd5e1" } } },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 18, bottom: 10, borderColor: isDark ? "#374151" : "#d1d5db", fillerColor: "rgba(37, 99, 235, 0.14)" },
      ],
      series: definitions.map((definition) => ({
        name: definition.name,
        type: "line",
        data: rows.map((row) => dailyReturnValue(row, definition.key)),
        symbol: "circle",
        showSymbol: false,
        symbolSize: 7,
        lineStyle: { width: definition.key === "excess_cumulative" ? 2.4 : 2.8, type: definition.lineType },
        emphasis: { focus: "series", scale: true },
      })),
    };
  }
  const rows = granularity === "monthly" ? item.monthly ?? [] : item.yearly ?? [];
  return {
    ...base,
    tooltip: { trigger: "axis", confine: true, appendToBody: true, formatter: (params: unknown) => periodBacktestTooltip(params, rows, definitions) },
    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(148, 163, 184, 0.12)" } },
    xAxis: { type: "category", data: rows.map((row) => row.period), axisLabel: { color: axisText }, axisLine: { lineStyle: { color: isDark ? "#374151" : "#cbd5e1" } } },
    series: definitions.map((definition) => ({
      name: definition.name,
      type: "bar",
      data: rows.map((row) => periodReturnValue(row, definition.key)),
      barMaxWidth: 34,
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    })),
  };
}

function EChart({
  option,
  className,
  onClick,
  onBlankClick,
}: {
  option: echarts.EChartsOption;
  className: string;
  onClick?: (payload: EChartsClickPayload) => void;
  onBlankClick?: (chart: echarts.ECharts, point: [number, number]) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    return () => {
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    const handler = (payload: EChartsClickPayload) => onClick(payload);
    chart.on("click", handler);
    return () => {
      chart.off("click", handler);
    };
  }, [onClick]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onBlankClick) return;
    const zrender = chart.getZr();
    const handler = (event: { offsetX: number; offsetY: number }) => {
      const point: [number, number] = [event.offsetX, event.offsetY];
      onBlankClick(chart, point);
    };
    zrender.on("click", handler);
    return () => {
      zrender.off("click", handler);
    };
  }, [onBlankClick]);

  useEffect(() => {
    const resize = () => chartRef.current?.resize();
    window.addEventListener("resize", resize);
    const observer = containerRef.current && "ResizeObserver" in window
      ? new ResizeObserver(resize)
      : null;
    if (containerRef.current && observer) observer.observe(containerRef.current);
    return () => {
      window.removeEventListener("resize", resize);
      observer?.disconnect();
    };
  }, []);

  return <div ref={containerRef} className={className} />;
}

function MetricCard({ label, value, note }: { label: string; value: string; note: string }) {
  const { isDark } = useColorMode();
  return (
    <div className={`rounded-lg border p-4 ${isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white"}`}>
      <div className={`text-xs ${isDark ? "text-gray-500" : "text-gray-500"}`}>{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${isDark ? "text-gray-100" : "text-gray-900"}`}>{value}</div>
      <div className={`mt-1 text-xs ${isDark ? "text-gray-500" : "text-gray-400"}`}>{note}</div>
    </div>
  );
}

export default function FactorMiningDashboard() {
  const { isDark } = useColorMode();
  const backtestPanelRef = useRef<HTMLElement>(null);
  const [viewName, setViewName] = useState<ViewName>("a-share");
  const [runs, setRuns] = useState<FactorMiningRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [runDetail, setRunDetail] = useState<FactorMiningRunDetail | null>(null);
  const [series, setSeries] = useState<FactorMiningBacktestSeries | null>(null);
  const [wqBoard, setWqBoard] = useState<WQResearchBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [wqLoading, setWqLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRowKey, setSelectedRowKey] = useState<string>("");
  const [universeFilter, setUniverseFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [minGrade, setMinGrade] = useState<FactorGrade>("D");
  const [searchText, setSearchText] = useState("");
  const [returnGranularity, setReturnGranularity] = useState<ReturnGranularity>("daily");
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadRuns() {
      setLoading(true);
      setError(null);
      try {
        const payload = await listFactorMiningRuns();
        if (cancelled) return;
        setRuns(payload.runs);
        if (payload.runs.length > 0) {
          setSelectedRunId(payload.runs[0].id);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "因子看板加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadRuns();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    async function loadDetail() {
      setDetailLoading(true);
      setError(null);
      try {
        const [detailPayload, seriesPayload] = await Promise.all([
          getFactorMiningRun(selectedRunId),
          getFactorMiningBacktestSeries(selectedRunId),
        ]);
        if (cancelled) return;
        setRunDetail(detailPayload);
        setSeries(seriesPayload);
        const firstRowKey = detailPayload.candidates[0]?.row_key ?? "";
        setSelectedRowKey(firstRowKey);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "因子批次加载失败");
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    }
    loadDetail();
    return () => { cancelled = true; };
  }, [selectedRunId]);

  useEffect(() => {
    if (viewName !== "wq" || wqBoard !== null || wqLoading) return;
    let cancelled = false;
    async function loadWq() {
      setWqLoading(true);
      setError(null);
      try {
        const payload = await getWqResearchBoard();
        if (!cancelled) setWqBoard(payload);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "WQ 候选加载失败");
      } finally {
        if (!cancelled) setWqLoading(false);
      }
    }
    loadWq();
    return () => { cancelled = true; };
  }, [viewName, wqBoard, wqLoading]);

  const universes = useMemo(() => {
    const values = new Set<string>();
    for (const run of runs) {
      if (run.universe) values.add(run.universe);
    }
    return Array.from(values);
  }, [runs]);

  const visibleRuns = useMemo(() => {
    if (universeFilter === "all") return runs;
    return runs.filter((run) => run.universe === universeFilter);
  }, [runs, universeFilter]);

  const filteredRows = useMemo(() => {
    const candidates = runDetail?.candidates ?? [];
    const text = searchText.trim().toLowerCase();
    return candidates
      .filter((candidate) => GRADE_RANK[candidate.grade] >= GRADE_RANK[minGrade])
      .filter((candidate) => {
        if (!text) return true;
        return `${candidate.name} ${candidate.expression} ${candidate.source_label}`.toLowerCase().includes(text);
      })
      .sort((left, right) => sortValue(right, sortKey) - sortValue(left, sortKey));
  }, [runDetail, searchText, minGrade, sortKey]);

  const selectedRow = useMemo(() => {
    if (!selectedRowKey) return filteredRows[0] ?? null;
    return runDetail?.candidates.find((candidate) => candidate.row_key === selectedRowKey) ?? filteredRows[0] ?? null;
  }, [filteredRows, runDetail, selectedRowKey]);

  const selectedBacktest = selectedRow ? series?.items[selectedRow.row_key] ?? null : null;

  const scatterData = useMemo<ScatterPoint[]>(() => {
    return filteredRows.map((candidate) => ({
      row_key: candidate.row_key,
      name: candidate.name || candidate.row_key,
      score: asNumber(candidate.score),
      ic_ir: asNumber(candidate.latest.ic_ir),
      ic_mean: asNumber(candidate.latest.ic_mean),
      strategy_sharpe: asNumber(candidate.latest.sharpe ?? candidate.latest.strategy_sharpe),
      cagr: asNumber(candidate.latest.cagr),
      max_drawdown: asNumber(candidate.latest.max_drawdown),
      turnover: asNumber(candidate.latest.turnover),
      grade: candidate.grade,
      expression: candidate.expression,
    }));
  }, [filteredRows]);

  const topBarOption = useMemo(() => barOption(filteredRows, isDark), [filteredRows, isDark]);
  const scatterChartOption = useMemo(
    () => scatterOption(scatterData, selectedRow?.row_key ?? "", isDark),
    [scatterData, selectedRow?.row_key, isDark],
  );

  const handleRefresh = useCallback(async () => {
    if (!selectedRunId || !selectedRow) return;
    setRefreshing(true);
    setError(null);
    try {
      const payload = await refreshFactorMiningBacktestSeries(selectedRunId, selectedRow.row_key);
      setSeries((previous) => {
        if (!previous) return previous;
        return {
          ...previous,
          items: {
            ...previous.items,
            [payload.row_key]: payload.item,
          },
          errors: payload.errors,
        };
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新回测失败");
    } finally {
      setRefreshing(false);
    }
  }, [selectedRunId, selectedRow]);

  const selectRow = useCallback((rowKey: string, shouldScroll: boolean) => {
    setSelectedRowKey(rowKey);
    if (shouldScroll) {
      window.requestAnimationFrame(() => {
        backtestPanelRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
      });
    }
  }, []);

  const handleBarClick = useCallback((payload: EChartsClickPayload) => {
    if (isBarChartPoint(payload.data)) selectRow(payload.data.row_key, true);
  }, [selectRow]);

  const handleScatterClick = useCallback((payload: EChartsClickPayload) => {
    if (isScatterChartPoint(payload.data)) selectRow(payload.data.row_key, true);
  }, [selectRow]);

  const handleScatterBlankClick = useCallback((chart: echarts.ECharts, point: [number, number]) => {
    if (!chart.containPixel({ gridIndex: 0 }, point)) return;
    const nearest = scatterData.reduce<{ row: ScatterPoint; distance: number } | null>((best, row) => {
      const pixel = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [row.ic_ir, row.score]);
      if (!Array.isArray(pixel)) return best;
      const distance = Math.hypot(Number(pixel[0]) - point[0], Number(pixel[1]) - point[1]);
      if (!best || distance < best.distance) return { row, distance };
      return best;
    }, null);
    if (nearest && nearest.distance <= 28) {
      selectRow(nearest.row.row_key, true);
    }
  }, [scatterData, selectRow]);

  const surface = isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white";
  const muted = isDark ? "text-gray-400" : "text-gray-500";
  const primary = isDark ? "text-gray-100" : "text-gray-900";

  if (loading) {
    return (
      <div className={`rounded-lg border p-8 text-center ${surface}`}>
        <Loader2 className="mx-auto h-5 w-5 animate-spin text-blue-500" />
        <p className={`mt-3 text-sm ${muted}`}>正在加载因子看板...</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className={`text-lg font-semibold ${primary}`}>因子看板</h2>
          <p className={`mt-1 text-sm ${muted}`}>集中查看 A股因子挖掘、历史反验、收益曲线和 WQ 候选状态。</p>
        </div>
        <div className={`inline-flex rounded-lg border p-1 ${isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white"}`}>
          <button
            type="button"
            onClick={() => setViewName("a-share")}
            className={`rounded-md px-3 py-1.5 text-sm ${viewName === "a-share" ? "bg-blue-600 text-white" : muted}`}
          >
            A股验证
          </button>
          <button
            type="button"
            onClick={() => setViewName("wq")}
            className={`rounded-md px-3 py-1.5 text-sm ${viewName === "wq" ? "bg-blue-600 text-white" : muted}`}
          >
            WQ候选
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {viewName === "a-share" ? (
        <div className="space-y-5">
          <div className={`grid gap-3 rounded-lg border p-4 md:grid-cols-[160px_minmax(260px,1fr)_150px_130px_minmax(180px,0.8fr)] ${surface}`}>
            <label className={`text-xs ${muted}`}>
              股票池
              <select
                value={universeFilter}
                onChange={(event) => {
                  const nextUniverse = event.target.value;
                  setUniverseFilter(nextUniverse);
                  const nextRun = runs.find((run) => nextUniverse === "all" || run.universe === nextUniverse);
                  if (nextRun) setSelectedRunId(nextRun.id);
                }}
                className={`mt-1 w-full rounded-md border px-2 py-2 text-sm ${isDark ? "border-gray-700 bg-gray-950 text-gray-100" : "border-gray-200 bg-white"}`}
              >
                <option value="all">全部</option>
                {universes.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>

            <label className={`text-xs ${muted}`}>
              数据
              <select
                value={selectedRunId}
                onChange={(event) => setSelectedRunId(event.target.value)}
                className={`mt-1 w-full rounded-md border px-2 py-2 text-sm ${isDark ? "border-gray-700 bg-gray-950 text-gray-100" : "border-gray-200 bg-white"}`}
              >
                {visibleRuns.map((run) => <option key={run.id} value={run.id}>{runLabel(run)}</option>)}
              </select>
            </label>

            <label className={`text-xs ${muted}`}>
              排序
              <select
                value={sortKey}
                onChange={(event) => setSortKey(event.target.value as SortKey)}
                className={`mt-1 w-full rounded-md border px-2 py-2 text-sm ${isDark ? "border-gray-700 bg-gray-950 text-gray-100" : "border-gray-200 bg-white"}`}
              >
                <option value="score">综合评分</option>
                <option value="ic_ir">ICIR</option>
                <option value="strategy_sharpe">Sharpe</option>
                <option value="max_drawdown">最大回撤</option>
                <option value="turnover">换手</option>
              </select>
            </label>

            <label className={`text-xs ${muted}`}>
              最低等级
              <select
                value={minGrade}
                onChange={(event) => setMinGrade(event.target.value as FactorGrade)}
                className={`mt-1 w-full rounded-md border px-2 py-2 text-sm ${isDark ? "border-gray-700 bg-gray-950 text-gray-100" : "border-gray-200 bg-white"}`}
              >
                <option value="D">D 以上</option>
                <option value="C">C 以上</option>
                <option value="B">B 以上</option>
                <option value="A">仅 A</option>
              </select>
            </label>

            <label className={`text-xs ${muted}`}>
              搜索
              <div className="relative mt-1">
                <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-gray-400" />
                <input
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="名称或表达式"
                  className={`w-full rounded-md border py-2 pl-8 pr-2 text-sm ${isDark ? "border-gray-700 bg-gray-950 text-gray-100" : "border-gray-200 bg-white"}`}
                />
              </div>
            </label>
          </div>

          {detailLoading ? (
            <div className={`rounded-lg border p-8 text-center ${surface}`}>
              <Loader2 className="mx-auto h-5 w-5 animate-spin text-blue-500" />
              <p className={`mt-3 text-sm ${muted}`}>正在加载因子批次...</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <MetricCard label="候选因子" value={String(filteredRows.length)} note="当前筛选结果" />
                <MetricCard label="A/B 级" value={String(filteredRows.filter((row) => row.grade === "A" || row.grade === "B").length)} note="高优先级候选" />
                <MetricCard label="平均 Score" value={fmtNumber(filteredRows.reduce((sum, row) => sum + asNumber(row.score), 0) / Math.max(1, filteredRows.length), 1)} note="综合评分均值" />
                <MetricCard label="最高 ICIR" value={fmtNumber(Math.max(...filteredRows.map((row) => asNumber(row.latest.ic_ir)), 0), 3)} note="最新窗口" />
                <MetricCard label="回测序列" value={String(Object.keys(series?.items ?? {}).length)} note="已生成收益曲线" />
              </div>

              <div className="grid gap-4 xl:grid-cols-[1.12fr_0.88fr]">
                <section className={`rounded-lg border ${surface}`}>
                  <div className="border-b border-gray-200 px-4 py-3">
                    <h3 className={`text-sm font-semibold ${primary}`}>Top 因子评分</h3>
                    <p className={`mt-1 text-xs ${muted}`}>条越长，综合评分越高。</p>
                  </div>
                  <div className="h-[280px] p-4">
                    {filteredRows.length ? (
                      <EChart option={topBarOption} className="h-full w-full" onClick={handleBarClick} />
                    ) : (
                      <div className={`flex h-full items-center justify-center text-sm ${muted}`}>没有符合条件的因子</div>
                    )}
                  </div>
                </section>

                <section className={`rounded-lg border ${surface}`}>
                  <div className="border-b border-gray-200 px-4 py-3">
                    <h3 className={`text-sm font-semibold ${primary}`}>Score 与 ICIR</h3>
                    <p className={`mt-1 text-xs ${muted}`}>点击散点定位到明细和收益曲线。</p>
                  </div>
                  <div className="h-[380px] p-4">
                    {scatterData.length ? (
                      <EChart
                        option={scatterChartOption}
                        className="h-full w-full"
                        onClick={handleScatterClick}
                        onBlankClick={handleScatterBlankClick}
                      />
                    ) : (
                      <div className={`flex h-full items-center justify-center text-sm ${muted}`}>没有可画图的数据</div>
                    )}
                  </div>
                </section>
              </div>

              <BacktestPanel
                panelRef={backtestPanelRef}
                item={selectedBacktest}
                row={selectedRow}
                granularity={returnGranularity}
                onGranularityChange={setReturnGranularity}
                onRefresh={handleRefresh}
                refreshing={refreshing}
              />

              <DetailTable
                rows={filteredRows}
                selectedRowKey={selectedRow?.row_key ?? ""}
                onSelect={(rowKey) => selectRow(rowKey, false)}
                onAsk={openAskGpt}
                isDark={isDark}
              />
            </>
          )}
        </div>
      ) : (
        <WqBoardView board={wqBoard} loading={wqLoading} isDark={isDark} />
      )}
    </div>
  );
}

function BacktestPanel({
  panelRef,
  item,
  row,
  granularity,
  onGranularityChange,
  onRefresh,
  refreshing,
}: {
  panelRef: RefObject<HTMLElement>;
  item: FactorMiningBacktestItem | null;
  row: FactorMiningCandidate | null;
  granularity: ReturnGranularity;
  onGranularityChange: (value: ReturnGranularity) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const { isDark } = useColorMode();
  const surface = isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white";
  const muted = isDark ? "text-gray-400" : "text-gray-500";
  const primary = isDark ? "text-gray-100" : "text-gray-900";
  const option = useMemo(() => item ? backtestOption(item, granularity, isDark) : null, [item, granularity, isDark]);

  return (
    <section ref={panelRef} className={`rounded-lg border ${surface}`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
        <div>
          <h3 className={`text-sm font-semibold ${primary}`}>回测收益</h3>
          <p className={`mt-1 text-xs ${muted}`}>{row ? `${row.name || row.row_key} · ${row.expression}` : "点击因子后查看收益曲线"}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRefresh}
            disabled={!row || refreshing}
            className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs disabled:opacity-50 ${isDark ? "border-gray-700 text-blue-300" : "border-gray-200 text-blue-700"}`}
          >
            {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            更新回测
          </button>
          {(["daily", "monthly", "yearly"] as ReturnGranularity[]).map((itemValue) => (
            <button
              key={itemValue}
              type="button"
              onClick={() => onGranularityChange(itemValue)}
              className={`rounded-md px-3 py-2 text-xs ${granularity === itemValue ? "bg-blue-600 text-white" : muted}`}
            >
              {itemValue === "daily" ? "日累计" : itemValue === "monthly" ? "月收益" : "年收益"}
            </button>
          ))}
        </div>
      </div>

      {item?.metrics ? (
        <div className="grid grid-cols-2 gap-3 p-4 md:grid-cols-5">
          <MetricCard label="总收益" value={fmtPercent(item.metrics.total_return, 2)} note="策略累计收益" />
          <MetricCard label="超额收益" value={fmtPercent(item.metrics.excess_total_return, 2)} note="相对基准" />
          <MetricCard label="CAGR" value={fmtPercent(item.metrics.cagr, 2)} note="年化收益" />
          <MetricCard label="Sharpe" value={fmtNumber(item.metrics.sharpe, 2)} note="收益风险比" />
          <MetricCard label="最大回撤" value={fmtPercent(item.metrics.max_drawdown, 2)} note="策略回撤" />
        </div>
      ) : null}

      <div className="h-96 px-4 pb-4">
        {item?.status === "failed" ? (
          <div className="flex h-full items-center justify-center text-sm text-red-600">{item.error_message ?? "回测未成功"}</div>
        ) : item && option ? (
          <EChart option={option} className="h-full w-full" />
        ) : (
          <div className={`flex h-full items-center justify-center text-sm ${muted}`}>暂无收益序列</div>
        )}
      </div>
      {item?.status === "success" && item.daily.length > 0 ? (
        <div className="px-4 pb-4">
          <RecentDailyTable rows={item.daily.slice(-8).reverse()} isDark={isDark} />
        </div>
      ) : null}
    </section>
  );
}

function RecentDailyTable({ rows, isDark }: { rows: FactorMiningDailyReturn[]; isDark: boolean }) {
  const muted = isDark ? "text-gray-400" : "text-gray-500";
  return (
    <div className={`overflow-auto rounded-lg border ${isDark ? "border-gray-800" : "border-gray-200"}`}>
      <table className="w-full min-w-[760px] text-xs">
        <thead className={isDark ? "bg-gray-950 text-gray-400" : "bg-gray-50 text-gray-500"}>
          <tr>
            <th className="px-3 py-2 text-left">日期</th>
            <th className="px-3 py-2 text-right">策略当日</th>
            <th className="px-3 py-2 text-right">基准当日</th>
            <th className="px-3 py-2 text-right">策略累计</th>
            <th className="px-3 py-2 text-right">基准累计</th>
            <th className="px-3 py-2 text-right">累计超额</th>
          </tr>
        </thead>
        <tbody className={isDark ? "divide-y divide-gray-800" : "divide-y divide-gray-100"}>
          {rows.map((row) => (
            <tr key={row.date}>
              <td className={`px-3 py-2 font-mono ${muted}`}>{row.date}</td>
              <td className={`px-3 py-2 text-right font-mono ${valueColorClass(row.strategy_return, isDark)}`}>{fmtPercent(row.strategy_return, 2)}</td>
              <td className={`px-3 py-2 text-right font-mono ${valueColorClass(row.benchmark_return, isDark)}`}>{fmtPercent(row.benchmark_return, 2)}</td>
              <td className={`px-3 py-2 text-right font-mono ${valueColorClass(row.strategy_cumulative, isDark)}`}>{fmtPercent(row.strategy_cumulative, 2)}</td>
              <td className={`px-3 py-2 text-right font-mono ${valueColorClass(row.benchmark_cumulative, isDark)}`}>{fmtPercent(row.benchmark_cumulative, 2)}</td>
              <td className={`px-3 py-2 text-right font-mono ${valueColorClass(row.excess_cumulative, isDark)}`}>{fmtPercent(row.excess_cumulative, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetailTable({
  rows,
  selectedRowKey,
  onSelect,
  onAsk,
  isDark,
}: {
  rows: FactorMiningCandidate[];
  selectedRowKey: string;
  onSelect: (rowKey: string) => void;
  onAsk: (row: FactorMiningCandidate) => void;
  isDark: boolean;
}) {
  const surface = isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white";
  const primary = isDark ? "text-gray-100" : "text-gray-900";
  const muted = isDark ? "text-gray-400" : "text-gray-500";
  const [sortState, setSortState] = useState<{ key: DetailSortKey; direction: SortDirection }>({
    key: "score",
    direction: "desc",
  });
  const headerTooltips: Record<DetailSortKey, string> = {
    grade: "综合等级，按 Score 分段：A 最强，B 可继续复核，C/D 需要谨慎。",
    name: "因子名称，方便在图表和明细里识别同一条候选。",
    expression: "因子表达式，描述实际计算信号的公式。",
    holdingPeriod: "持仓周期，表示每次调仓后持有多少个交易日。",
    score: "综合评分，越高代表当前回测里的稳定性、收益、IC 和风险表现越好。",
    latestScore: "最新窗口评分，主要对应最近一段验证窗口的表现。",
    historyScore: "历史反验评分，主要对应较早窗口；没有反验数据时显示为空。",
    ic: "Rank IC 均值，衡量因子排序和未来收益排序的一致性，正值越高越好。",
    icir: "ICIR，即 IC 均值除以 IC 波动，衡量 IC 是否稳定；越高越稳定。",
    winRate: "IC 胜率，表示多少比例的观察期里 IC 为正。",
    monotonicity: "分组单调性，衡量因子分组收益是否从低到高有规律地变化。",
    sharpe: "Top 组策略 Sharpe，衡量收益相对波动的性价比，越高越好。",
    longShortSharpe: "多空 Sharpe，Top 组减 Bottom 组后的收益风险比，用来看因子区分度。",
    turnover: "平均换手率，越高表示调仓越频繁，交易成本压力越大。",
    cagr: "年化收益率，表示按当前回测结果折算到一年的收益。",
    maxDrawdown: "最大回撤，表示净值从高点到低点的最大跌幅，越接近 0 风险越小。",
    flipped: "是否自动翻转方向；是表示低因子值组表现更好，使用前要显式修正方向。",
  };
  const defaultDirections: Record<DetailSortKey, SortDirection> = {
    grade: "desc",
    name: "asc",
    expression: "asc",
    holdingPeriod: "asc",
    score: "desc",
    latestScore: "desc",
    historyScore: "desc",
    ic: "desc",
    icir: "desc",
    winRate: "desc",
    monotonicity: "desc",
    sharpe: "desc",
    longShortSharpe: "desc",
    turnover: "asc",
    cagr: "desc",
    maxDrawdown: "desc",
    flipped: "desc",
  };
  const displayRows = useMemo(
    () => sortRowsByDetail(rows, sortState.key, sortState.direction),
    [rows, sortState],
  );
  const toggleSort = (key: DetailSortKey) => {
    setSortState((previous) => {
      if (previous.key === key) {
        return { key, direction: previous.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: defaultDirections[key] };
    });
  };
  const sortIndicator = (key: DetailSortKey): string => {
    if (sortState.key !== key) return "↕";
    return sortState.direction === "asc" ? "↑" : "↓";
  };
  const HeaderCell = ({
    label,
    sortKey,
    align,
  }: {
    label: string;
    sortKey: DetailSortKey;
    align: "left" | "right";
  }) => (
    <th
      tabIndex={0}
      title={headerTooltips[sortKey]}
      aria-sort={sortState.key === sortKey ? (sortState.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => toggleSort(sortKey)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggleSort(sortKey);
        }
      }}
      className={`group relative cursor-pointer px-3 py-2 select-none ${align === "right" ? "text-right" : "text-left"}`}
    >
      <span className={`inline-flex items-center gap-1 ${align === "right" ? "justify-end" : "justify-start"}`}>
        {label}
        <span className="rounded-full border border-current px-1 text-[10px] leading-4 opacity-60">?</span>
        <span className="font-mono text-[10px] opacity-70">{sortIndicator(sortKey)}</span>
      </span>
      <span className={`pointer-events-none absolute top-full z-30 mt-1 hidden w-64 rounded-md border px-2 py-1.5 text-left text-xs font-normal shadow-lg group-hover:block group-focus:block ${align === "right" ? "right-2" : "left-2"} ${isDark ? "border-gray-700 bg-gray-950 text-gray-300" : "border-gray-200 bg-white text-gray-600"}`}>
        {headerTooltips[sortKey]}
      </span>
    </th>
  );
  return (
    <section className={`rounded-lg border ${surface}`}>
      <div className="border-b border-gray-200 px-4 py-3">
        <h3 className={`text-sm font-semibold ${primary}`}>完整明细</h3>
        <p className={`mt-1 text-xs ${muted}`}>点击行查看该因子的收益曲线。</p>
      </div>
      <div className="max-h-[640px] overflow-auto">
        <table className="w-full min-w-[1540px] text-sm">
          <thead className={isDark ? "bg-gray-950 text-gray-400" : "bg-gray-50 text-gray-500"}>
            <tr>
              <HeaderCell label="等级" sortKey="grade" align="left" />
              <HeaderCell label="因子" sortKey="name" align="left" />
              <HeaderCell label="表达式" sortKey="expression" align="left" />
              <HeaderCell label="持仓" sortKey="holdingPeriod" align="right" />
              <HeaderCell label="Score" sortKey="score" align="right" />
              <HeaderCell label="最新Score" sortKey="latestScore" align="right" />
              <HeaderCell label="历史Score" sortKey="historyScore" align="right" />
              <HeaderCell label="IC" sortKey="ic" align="right" />
              <HeaderCell label="ICIR" sortKey="icir" align="right" />
              <HeaderCell label="胜率" sortKey="winRate" align="right" />
              <HeaderCell label="单调性" sortKey="monotonicity" align="right" />
              <HeaderCell label="Sharpe" sortKey="sharpe" align="right" />
              <HeaderCell label="多空Sharpe" sortKey="longShortSharpe" align="right" />
              <HeaderCell label="换手" sortKey="turnover" align="right" />
              <HeaderCell label="CAGR" sortKey="cagr" align="right" />
              <HeaderCell label="MaxDD" sortKey="maxDrawdown" align="right" />
              <HeaderCell label="翻转" sortKey="flipped" align="right" />
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody className={isDark ? "divide-y divide-gray-800" : "divide-y divide-gray-100"}>
            {displayRows.map((row) => (
              <tr
                key={row.row_key}
                onClick={() => onSelect(row.row_key)}
                className={`cursor-pointer ${selectedRowKey === row.row_key ? (isDark ? "bg-blue-500/10" : "bg-blue-50") : ""}`}
              >
                <td className="px-3 py-3"><span className={`inline-flex rounded-md border px-2 py-0.5 text-xs font-bold ${gradeClass(row.grade, isDark)}`}>{row.grade}</span></td>
                <td className="px-3 py-3">
                  <div className={`font-medium ${primary}`}>{row.name || row.row_key}</div>
                </td>
                <td className={`px-3 py-3 max-w-[440px] whitespace-normal font-mono text-xs ${muted}`}>
                  <div>{row.expression}</div>
                </td>
                <td className="px-3 py-3 text-right font-mono">{fmtNumber(row.holding_period, 0)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtNumber(row.score, 1)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtNumber(row.latest_score, 1)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtNumber(row.history_score, 1)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.ic_mean, isDark)}`}>{fmtNumber(row.latest.ic_mean, 4)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.ic_ir, isDark)}`}>{fmtNumber(row.latest.ic_ir, 3)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtPercent(row.latest.ic_win_rate, 1)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtNumber(row.latest.monotonicity, 2)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.sharpe ?? row.latest.strategy_sharpe, isDark)}`}>{fmtNumber(row.latest.sharpe ?? row.latest.strategy_sharpe, 3)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.long_short_sharpe, isDark)}`}>{fmtNumber(row.latest.long_short_sharpe, 3)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtPercent(row.latest.turnover, 2)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.cagr, isDark)}`}>{fmtPercent(row.latest.cagr, 2)}</td>
                <td className={`px-3 py-3 text-right font-mono ${valueColorClass(row.latest.max_drawdown, isDark)}`}>{fmtPercent(row.latest.max_drawdown, 2)}</td>
                <td className="px-3 py-3 text-right">{row.latest.flipped === true ? "是" : "否"}</td>
                <td className="px-3 py-3 text-right">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onAsk(row);
                    }}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-blue-700"
                  >
                    <ExternalLink className="h-3 w-3" />
                    Ask GPT
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function WqBoardView({ board, loading, isDark }: { board: WQResearchBoard | null; loading: boolean; isDark: boolean }) {
  const surface = isDark ? "border-gray-800 bg-gray-900" : "border-gray-200 bg-white";
  const primary = isDark ? "text-gray-100" : "text-gray-900";
  const muted = isDark ? "text-gray-400" : "text-gray-500";

  if (loading) {
    return (
      <div className={`rounded-lg border p-8 text-center ${surface}`}>
        <Loader2 className="mx-auto h-5 w-5 animate-spin text-blue-500" />
        <p className={`mt-3 text-sm ${muted}`}>正在加载 WQ 候选...</p>
      </div>
    );
  }

  if (!board) {
    return <div className={`rounded-lg border p-8 text-center text-sm ${surface} ${muted}`}>暂无 WQ 候选数据</div>;
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <MetricCard label="候选数量" value={String(board.summary.candidate_count)} note="已记录模拟结果" />
        <MetricCard label="待确认" value={String(board.summary.ready_to_submit)} note="达到提交门槛" />
        <MetricCard label="接近门槛" value={String(board.summary.near_ready)} note="可继续观察" />
        <MetricCard label="已提交" value={String(board.summary.submitted_count)} note="Alpha 记录" />
        <MetricCard label="ACTIVE" value={String(board.summary.active_count)} note="平台状态" />
        <MetricCard label="失败" value={String(board.summary.failed_count)} note="需复盘" />
      </div>

      <section className={`rounded-lg border ${surface}`}>
        <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
          <Sparkles className="h-4 w-4 text-blue-500" />
          <div>
            <h3 className={`text-sm font-semibold ${primary}`}>WQ 候选</h3>
            <p className={`mt-1 text-xs ${muted}`}>先模拟和记录，达到门槛后再确认提交。</p>
          </div>
        </div>
        <div className="max-h-[520px] overflow-auto">
          <table className="w-full min-w-[1100px] text-sm">
            <thead className={isDark ? "bg-gray-950 text-gray-400" : "bg-gray-50 text-gray-500"}>
              <tr>
                <th className="px-3 py-2 text-left">表达式</th>
                <th className="px-3 py-2 text-right">决策</th>
                <th className="px-3 py-2 text-right">Fitness</th>
                <th className="px-3 py-2 text-right">Sharpe</th>
                <th className="px-3 py-2 text-right">Returns</th>
                <th className="px-3 py-2 text-right">Turnover</th>
                <th className="px-3 py-2 text-right">Alpha</th>
                <th className="px-3 py-2 text-right">状态</th>
              </tr>
            </thead>
            <tbody className={isDark ? "divide-y divide-gray-800" : "divide-y divide-gray-100"}>
              {board.candidates.map((item) => (
                <tr key={`${item.task_id}-${item.combo_key ?? item.alpha_id ?? item.expression}`}>
                  <td className="px-3 py-3">
                    <div className={`font-mono text-xs ${primary}`}>{item.expression}</div>
                    <div className={`mt-1 text-xs ${muted}`}>{item.region} · {item.universe} · Delay {item.delay ?? "-"}</div>
                  </td>
                  <td className="px-3 py-3 text-right">{item.decision}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtNumber(item.fitness, 3)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtNumber(item.sharpe, 2)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtPercent(item.returns, 2)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtPercent(item.turnover, 2)}</td>
                  <td className="px-3 py-3 text-right font-mono text-xs">{item.alpha_id ?? "-"}</td>
                  <td className="px-3 py-3 text-right">{item.status_label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className={`rounded-lg border ${surface}`}>
        <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
          <Brain className="h-4 w-4 text-amber-500" />
          <div>
            <h3 className={`text-sm font-semibold ${primary}`}>已提交 Alpha</h3>
            <p className={`mt-1 text-xs ${muted}`}>跟踪提交后的 SC 和 ACTIVE 状态。</p>
          </div>
        </div>
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full min-w-[980px] text-sm">
            <thead className={isDark ? "bg-gray-950 text-gray-400" : "bg-gray-50 text-gray-500"}>
              <tr>
                <th className="px-3 py-2 text-left">Alpha</th>
                <th className="px-3 py-2 text-right">状态</th>
                <th className="px-3 py-2 text-right">Fitness</th>
                <th className="px-3 py-2 text-right">Sharpe</th>
                <th className="px-3 py-2 text-right">Returns</th>
                <th className="px-3 py-2 text-right">Turnover</th>
                <th className="px-3 py-2 text-right">提交时间</th>
              </tr>
            </thead>
            <tbody className={isDark ? "divide-y divide-gray-800" : "divide-y divide-gray-100"}>
              {board.submitted_alphas.map((item) => (
                <tr key={item.alpha_id}>
                  <td className="px-3 py-3">
                    <div className={`font-mono text-xs ${primary}`}>{item.alpha_id}</div>
                    <div className={`mt-1 max-w-[560px] truncate font-mono text-xs ${muted}`}>{item.expression}</div>
                  </td>
                  <td className="px-3 py-3 text-right">{item.status_label}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtNumber(item.fitness, 3)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtNumber(item.sharpe, 2)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtPercent(item.returns, 2)}</td>
                  <td className="px-3 py-3 text-right font-mono">{fmtPercent(item.turnover, 2)}</td>
                  <td className="px-3 py-3 text-right text-xs">{fmtDate(item.submitted_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
