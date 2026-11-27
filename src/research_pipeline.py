"""Nightly research run: Scout -> Ideas -> Quant -> Skeptic -> Rank.
Produces research/scan.md, research/ideas.md, research/killed.md,
research/shortlist.md + shortlist.json (machine-readable, read by
execution.py the next morning), and research/backtests/*.json.
"""

import json
import logging
import statistics
from pathlib import Path

from .alpaca_client import AlpacaClient
from .config import Config
from .ideas import CANDIDATE_STRATEGIES
from .rank import score_report, write_research_outputs
from .scout import scan_tonight
from .skeptic import run_gates
from .universe import UNIVERSE

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"


def _kelly_pct(trades) -> float:
    wins = [t.r_multiple for t in trades if t.r_multiple > 0]
    losses = [abs(t.r_multiple) for t in trades if t.r_multiple <= 0]
    if not trades or not losses or not wins:
        return 0.0
    win_rate = len(wins) / len(trades)
    b = statistics.mean(wins) / statistics.mean(losses)
    if b <= 0:
        return 0.0
    return max(0.0, win_rate - (1 - win_rate) / b)


def run() -> None:
    config = Config()
    config.validate()

    broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)

    logger.info("Fetching %d years of daily bars for %d symbols", config.backtest_years, len(UNIVERSE))
    symbol_bars = broker.get_daily_bars(UNIVERSE, years=config.backtest_years)
    logger.info("Got bars for %d/%d symbols", len(symbol_bars), len(UNIVERSE))

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    tonight = scan_tonight(symbol_bars)
    (RESEARCH_DIR / "tonight_signals.json").write_text(json.dumps(tonight, indent=2))
    scan_lines = [f"# Scan -- {len(tonight)} anomalies found tonight\n"]
    for item in tonight:
        scan_lines.append(f"- {item['symbol']}: {item['anomaly']} on {item['date']}\n")
    (RESEARCH_DIR / "scan.md").write_text("".join(scan_lines))
    logger.info("Scout found %d anomalies across the universe tonight", len(tonight))

    ideas_lines = ["# Ideas under test\n"]
    for strategy in CANDIDATE_STRATEGIES:
        ideas_lines.append(
            f"\n## {strategy.name} ({strategy.direction})\n"
            f"- Trigger: {strategy.anomaly_column}\n"
            f"- Stop: {strategy.stop_pct:.1%}, Target: "
            f"{f'{strategy.target_pct:.1%}' if strategy.target_pct else 'none (time exit only)'}, "
            f"max hold: {strategy.max_holding_days}d\n"
            f"- Reason: {strategy.reason}\n"
            f"- Losing side: {strategy.losing_side}\n"
        )
    (RESEARCH_DIR / "ideas.md").write_text("".join(ideas_lines))

    reports = []
    for strategy in CANDIDATE_STRATEGIES:
        logger.info("Running Skeptic gates on %s", strategy.name)
        report = run_gates(strategy, symbol_bars, min_trades=config.min_backtest_trades)
        for gate in report.gates:
            logger.info(
                "  [%s] %s: %s", "PASS" if gate.passed else "FAIL", gate.name, gate.detail
            )
        reports.append(report)

    scores = {
        r.strategy_name: score_report(r, config.min_backtest_trades)
        for r in reports if r.passed_all
    }

    shortlisted_names = write_research_outputs(
        reports, scores, config.min_shortlist_score, RESEARCH_DIR
    )
    logger.info(
        "%d/%d ideas survived all 7 gates; %d made the shortlist (score >= %.1f)",
        sum(1 for r in reports if r.passed_all), len(reports),
        len(shortlisted_names), config.min_shortlist_score,
    )

    strategy_by_name = {s.name: s for s in CANDIDATE_STRATEGIES}
    shortlist_json = []
    for report in reports:
        if report.strategy_name not in shortlisted_names:
            continue
        strategy = strategy_by_name[report.strategy_name]
        mean_r = (
            statistics.mean(t.r_multiple for t in report.trades) if report.trades else 0.0
        )
        shortlist_json.append(
            {
                "name": report.strategy_name,
                "anomaly_column": strategy.anomaly_column,
                "direction": strategy.direction,
                "stop_pct": strategy.stop_pct,
                "target_pct": strategy.target_pct,
                "max_holding_days": strategy.max_holding_days,
                "score": scores[report.strategy_name].total,
                "mean_r": mean_r,
                "kelly_pct": _kelly_pct(report.trades),
            }
        )
    (RESEARCH_DIR / "shortlist.json").write_text(json.dumps(shortlist_json, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
