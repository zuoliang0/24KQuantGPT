"""Report generation + metrics extraction — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT
"""

import logging

import matplotlib

matplotlib.use("Agg")  # non-interactive backend, must be before any pyplot import
import re
from html import escape
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def generate_report(
    ls_returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    title: str = "Factor Long-Short Backtest",
    output_dir: str | None = None,
    periods_per_year: int = 252,
) -> dict:
    """Generate QuantStats HTML report and extract key metrics.

    Args:
        ls_returns: Daily long-short return series indexed by date.
        benchmark_returns: Optional benchmark daily returns for comparison.
        title: Report title.
        output_dir: Directory for HTML output. Defaults to <project>/reports.

    Returns:
        Dict with report_path and metrics.
    """
    import quantstats as qs

    output_dir = Path(output_dir) if output_dir else (_PROJECT_ROOT / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    returns = ls_returns.sort_index().copy()
    returns.index = pd.to_datetime(returns.index).normalize()
    returns.name = "Strategy"

    if benchmark_returns is not None:
        benchmark_returns = benchmark_returns.copy()
        benchmark_returns.index = pd.to_datetime(benchmark_returns.index).normalize()
        benchmark_returns = benchmark_returns.sort_index()
        # Align benchmark to returns dates
        bm_aligned = benchmark_returns.reindex(returns.index, method="ffill")
        valid = ~bm_aligned.isna()
        if valid.sum() < 2:
            logger.warning("Insufficient benchmark overlap, generating report without benchmark")
            benchmark_returns = None
        else:
            returns = returns[valid]
            benchmark_returns = bm_aligned[valid]

    # Generate HTML
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    report_path = str(output_dir / f"backtest_report_{timestamp}.html")

    qs.reports.html(
        returns,
        benchmark=benchmark_returns,
        output=report_path,
        title=title,
        rf=0.03,
        match_dates=False,
        periods_per_year=periods_per_year,
    )

    # 修补 QuantStats HTML：适配内嵌布局，并补充中文图表解读。
    _patch_report_html(report_path)

    logger.info(f"Report saved: {report_path}")

    # Extract metrics
    metrics = {
        "total_return": float(qs.stats.comp(returns)),
        "cagr": float(qs.stats.cagr(returns, periods=periods_per_year)),
        "sharpe": float(qs.stats.sharpe(returns, rf=0.03, periods=periods_per_year)),
        "sortino": float(qs.stats.sortino(returns, rf=0.03, periods=periods_per_year)),
        "max_drawdown": float(qs.stats.max_drawdown(returns)),
        "volatility": float(qs.stats.volatility(returns, periods=periods_per_year)),
        "win_rate": float(qs.stats.win_rate(returns)),
        "profit_factor": float(qs.stats.profit_factor(returns)),
    }

    if benchmark_returns is not None:
        metrics["benchmark_total_return"] = float(qs.stats.comp(benchmark_returns))
        metrics["benchmark_cagr"] = float(qs.stats.cagr(benchmark_returns, periods=periods_per_year))

    return {"report_path": report_path, "metrics": metrics}


_CHART_GUIDES: dict[str, dict[str, str]] = {
    "Cumulative Returns vs Benchmark": {
        "zh": "累计收益对比",
        "en": "Cumulative Returns",
        "tip": "查看策略净值是否长期跑赢基准。曲线越平稳向上越好，如果大部分时间低于基准，说明因子优势不明显。",
    },
    "Cumulative Returns vs Benchmark (Log Scaled)": {
        "zh": "对数累计收益对比",
        "en": "Log Scaled Cumulative Returns",
        "tip": "用对数刻度观察长期复利变化，更容易比较不同阶段的增长速度和回撤后的恢复能力。",
    },
    "Cumulative Returns vs Benchmark (Volatility Matched)": {
        "zh": "波动率匹配收益对比",
        "en": "Volatility Matched Returns",
        "tip": "把策略和基准调整到相近波动水平后再比较收益，用来判断超额收益是否只是来自更高风险暴露。",
    },
    "EOY Returns  vs Benchmark": {
        "zh": "年度收益对比",
        "en": "EOY Returns",
        "tip": "逐年比较策略和基准表现。若只有少数年份好，说明稳定性不足；若多数年份领先，因子更值得继续研究。",
    },
    "Distribution of Monthly Returns": {
        "zh": "月收益分布",
        "en": "Monthly Return Distribution",
        "tip": "观察月度收益集中在哪些区间。分布越偏右越好，左侧长尾越明显，说明极端亏损月份风险越高。",
    },
    "Daily Returns (Cumulative Sum)": {
        "zh": "日收益累积",
        "en": "Daily Returns",
        "tip": "查看每日收益累加后的路径，用来发现收益是否集中在少数时间段，或是否长期横盘震荡。",
    },
    "Rolling Beta to Benchmark": {
        "zh": "滚动 Beta",
        "en": "Rolling Beta",
        "tip": "衡量策略相对基准的市场暴露。Beta 越高，越像在跟随指数；Beta 接近 0 时，更偏独立因子收益。",
    },
    "Rolling Volatility (6-Months)": {
        "zh": "滚动波动率",
        "en": "Rolling Volatility",
        "tip": "查看近 6 个月风险水平如何变化。波动率突然升高时，需要检查是否遇到不适合该因子的市场环境。",
    },
    "Rolling Sharpe (6-Months)": {
        "zh": "滚动夏普",
        "en": "Rolling Sharpe",
        "tip": "查看近 6 个月风险调整收益。长期为正且较稳定更好，频繁跌到 0 以下说明因子阶段性失效明显。",
    },
    "Rolling Sortino (6-Months)": {
        "zh": "滚动索提诺",
        "en": "Rolling Sortino",
        "tip": "只惩罚下行波动的风险调整收益。它比夏普更关注亏损波动，适合判断下跌风险是否可控。",
    },
    "Strategy - Worst 5 Drawdown Periods": {
        "zh": "最差回撤区间",
        "en": "Worst Drawdowns",
        "tip": "标出策略历史上最难受的几个回撤阶段。重点看最大跌幅、持续时间和恢复速度。",
    },
    "Underwater Plot": {
        "zh": "回撤水下图",
        "en": "Underwater Plot",
        "tip": "展示策略距离历史高点的亏损幅度。曲线越深、持续越久，持有体验越差。",
    },
    "Strategy - Monthly Returns (%)": {
        "zh": "月度收益热力图",
        "en": "Monthly Returns",
        "tip": "按年月查看收益热区和冷区，方便识别因子在哪些市场阶段更容易赚钱或亏钱。",
    },
    "Strategy - Return Quantiles": {
        "zh": "收益分位数",
        "en": "Return Quantiles",
        "tip": "比较日、周、月等不同周期收益的分位数，用来理解正常波动范围和尾部风险。",
    },
}

_TABLE_HEADING_REPLACEMENTS: dict[str, str] = {
    "<h3>Key Performance Metrics</h3>": "<h3>核心绩效指标 <small>(Key Performance Metrics)</small></h3>",
    "<h3>EOY Returns vs Benchmark</h3>": "<h3>年度收益对比 <small>(EOY Returns)</small></h3>",
    "<h3>Worst 10 Drawdowns</h3>": "<h3>最差 10 次回撤 <small>(Worst Drawdowns)</small></h3>",
}


_CSS_PATCH = """
<style>
/* QuantGPT: fix layout for iframe embedding */
body { margin: 15px !important; }
.container { max-width: 100% !important; display: flex; flex-wrap: wrap; gap: 0; }
.container > h1, .container > h4, .container > hr { width: 100%; flex-shrink: 0; }
#left { float: none !important; width: 62% !important; min-width: 0; margin-right: 0 !important; margin-top: -1.2rem; }
#right { float: none !important; width: 36% !important; min-width: 280px; }
#left svg { width: 100% !important; height: auto !important; }
h3 small, .qg-chart-title small { color: #64748b; font-size: 0.78em; font-weight: 400; margin-left: 4px; }
.qg-chart-caption {
    position: relative;
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 12px 0 4px;
    color: #111827;
    font-size: 15px;
    font-weight: 700;
}
.qg-chart-help {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border-radius: 999px;
    background: #eef2ff;
    color: #2563eb;
    cursor: help;
    font-size: 12px;
    font-weight: 700;
}
.qg-chart-tooltip {
    display: none;
    position: absolute;
    z-index: 20;
    left: 0;
    top: 26px;
    max-width: min(520px, 92vw);
    padding: 10px 12px;
    border: 1px solid #dbe3ef;
    border-radius: 8px;
    background: #ffffff;
    color: #334155;
    box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.6;
}
.qg-chart-caption:hover .qg-chart-tooltip,
.qg-chart-caption:focus-within .qg-chart-tooltip {
    display: block;
}
@media (max-width: 700px) {
    #left, #right { width: 100% !important; }
}
</style>
"""


def _chart_caption(chart: dict[str, str]) -> str:
    title = escape(chart["zh"])
    english = escape(chart["en"])
    tip = escape(chart["tip"])
    return (
        '<div class="qg-chart-caption" tabindex="0">'
        f'<span class="qg-chart-title">{title} <small>({english})</small></span>'
        '<span class="qg-chart-help" aria-hidden="true">?</span>'
        f'<span class="qg-chart-tooltip">{tip}</span>'
        "</div>\n"
    )


def _svg_accessibility_title(chart: dict[str, str]) -> str:
    return f"{chart['zh']} ({chart['en']})：{chart['tip']}"


def _detect_chart(svg: str) -> dict[str, str] | None:
    for english_title, chart in _CHART_GUIDES.items():
        if f"<!-- {english_title} -->" in svg:
            return chart
    return None


def _patch_svg_charts(html: str) -> str:
    if "qg-chart-caption" in html:
        return html

    def replace_svg(match: re.Match[str]) -> str:
        svg = match.group(0)
        chart = _detect_chart(svg)
        if chart is None:
            return svg
        accessible_title = escape(_svg_accessibility_title(chart))
        svg_with_title = re.sub(
            r"(<svg\b[^>]*)(>)",
            rf'\1 role="img" aria-label="{accessible_title}"\2\n<title>{accessible_title}</title>',
            svg,
            count=1,
        )
        return _chart_caption(chart) + svg_with_title

    return re.sub(r"<svg\b[\s\S]*?</svg>", replace_svg, html)


def _patch_table_headings(html: str) -> str:
    patched = html
    for source, replacement in _TABLE_HEADING_REPLACEMENTS.items():
        patched = patched.replace(source, replacement)
    return patched


def _patch_report_html(report_path: str) -> None:
    """向 QuantStats HTML 注入响应式样式和学习型图表解读。"""
    try:
        path = Path(report_path)
        html = path.read_text(encoding="utf-8")
        html = _patch_svg_charts(html)
        html = _patch_table_headings(html)
        if "</head>" in html:
            html = html.replace("</head>", _CSS_PATCH + "</head>", 1)
        path.write_text(html, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to patch report HTML: {e}")
