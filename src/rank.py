"""Rank: scores every strategy that survived all 7 Skeptic gates, 0-10
(Evidence 0-4 / Fit 0-3 / Trade quality 0-3), and writes the human-readable
research/ output files. Strategies that failed a gate never reach this
module's scoring -- they go straight into killed.md with the gate that
killed them.

Scope note: the full spec's "Fit" score is a regime-match (trend/range/vol
vs. today's regime). This simplified version scores Fit on *time-consistency*
instead -- how much the edge held up in the most recent (out-of-sample) slice
vs. the earlier slice of the same backtest -- which is cheaper to compute
reliably and catches the same failure mode (an edge that's actually decayed)
even without a full regime classifier.
"""

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from .backtest import Trade
from .skeptic import SkepticReport


@dataclass(frozen=True)
class ScoreBreakdown:
    evidence: float
    fit: float
    trade_quality: float

    @property
    def total(self) -> float:
        return self.evidence + self.fit + self.trade_quality


def _mean_r(trades: list[Trade]) -> float:
    return statistics.mean(t.r_multiple for t in trades) if trades else 0.0


def score_report(report: SkepticReport, min_trades: int) -> ScoreBreakdown:
    trades = report.trades
    mean_r = _mean_r(trades)

    evidence = 2.0
    if len(trades) >= 2 * min_trades:
        evidence += 1.0
    luck_gate = next((g for g in report.gates if g.name == "luck_check"), None)
    if luck_gate and luck_gate.passed and "mean R=" in luck_gate.detail:
        worst_5pct = float(luck_gate.detail.rsplit("mean R=", 1)[1])
        if worst_5pct > 0.1:
            evidence += 1.0
    evidence = min(evidence, 4.0)

    ordered = sorted(trades, key=lambda t: t.entry_date)
    split = int(len(ordered) * 0.7)
    train_mean_r = _mean_r(ordered[:split])
    test_mean_r = _mean_r(ordered[split:])
    if train_mean_r > 0 and test_mean_r >= train_mean_r * 0.5:
        fit = 3.0
    elif test_mean_r > 0:
        fit = 2.0
    else:
        fit = 0.0

    if mean_r >= 0.5:
        trade_quality = 3.0
    elif mean_r >= 0.25:
        trade_quality = 2.0
    elif mean_r > 0:
        trade_quality = 1.0
    else:
        trade_quality = 0.0

    return ScoreBreakdown(evidence=evidence, fit=fit, trade_quality=trade_quality)


def write_research_outputs(
    reports: list[SkepticReport],
    scores: dict[str, ScoreBreakdown],
    min_shortlist_score: float,
    research_dir: Path,
) -> list[str]:
    """Writes research/shortlist.md, research/killed.md, research/strategies/,
    research/backtests/. Returns the list of strategy names that made the
    shortlist (score >= min_shortlist_score)."""
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / "strategies").mkdir(exist_ok=True)
    (research_dir / "backtests").mkdir(exist_ok=True)

    survivors = [r for r in reports if r.passed_all]
    killed = [r for r in reports if not r.passed_all]

    shortlisted: list[str] = []
    shortlist_lines = ["# Shortlist\n", f"Generated from {len(reports)} candidate ideas.\n"]
    for report in sorted(survivors, key=lambda r: scores[r.strategy_name].total, reverse=True):
        score = scores[report.strategy_name]
        mean_r = _mean_r(report.trades)
        line = (
            f"\n## {report.strategy_name} -- score {score.total:.1f}/10\n"
            f"- Evidence: {score.evidence:.1f}/4, Fit: {score.fit:.1f}/3, "
            f"Trade quality: {score.trade_quality:.1f}/3\n"
            f"- {len(report.trades)} out-of-sample trades, mean R = {mean_r:.3f}\n"
        )
        shortlist_lines.append(line)
        if score.total >= min_shortlist_score:
            shortlisted.append(report.strategy_name)

    if not shortlisted:
        shortlist_lines.append(
            f"\n**No strategy scored >= {min_shortlist_score}. No trade today -- "
            "that's a valid outcome, not a bug.**\n"
        )
    (research_dir / "shortlist.md").write_text("".join(shortlist_lines))

    killed_lines = ["# Killed ideas\n"]
    for report in killed:
        failed = report.first_failed
        killed_lines.append(
            f"\n## {report.strategy_name}\n"
            f"- Killed by: **{failed.name}**\n"
            f"- {failed.detail}\n"
        )
    (research_dir / "killed.md").write_text("".join(killed_lines))

    for report in reports:
        score = scores.get(report.strategy_name)
        payload = {
            "strategy_name": report.strategy_name,
            "passed_all": report.passed_all,
            "score": score.total if score else None,
            "gates": [{"name": g.name, "passed": g.passed, "detail": g.detail} for g in report.gates],
            "num_trades": len(report.trades),
            "mean_r": _mean_r(report.trades),
        }
        (research_dir / "backtests" / f"{report.strategy_name}.json").write_text(
            json.dumps(payload, indent=2)
        )

    return shortlisted
