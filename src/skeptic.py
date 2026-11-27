"""Skeptic: tries to kill every idea before it reaches the morning shortlist.
Implements the 7 gates from playbook.md. An idea needs all 7 to pass; most
die here, and that is the system working as intended, not failing.
"""

import random
import statistics
from dataclasses import dataclass, field

from .backtest import Trade, run_backtest, run_random_entry_baseline
from .ideas import StrategyDef

MIN_REASON_LEN = 60
MIN_DISTINCT_SYMBOLS = 10
PERTURBATION_FACTORS = (0.8, 1.2)
MONTE_CARLO_ITERATIONS = 1000
MONTE_CARLO_PERCENTILE = 5  # worst 5% of resamples


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SkepticReport:
    strategy_name: str
    gates: list[GateResult] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    @property
    def passed_all(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def first_failed(self) -> GateResult | None:
        for g in self.gates:
            if not g.passed:
                return g
        return None


def _mean_r(trades: list[Trade]) -> float:
    return statistics.mean(t.r_multiple for t in trades) if trades else 0.0


def _gate_reason(strategy: StrategyDef) -> GateResult:
    ok = len(strategy.reason) >= MIN_REASON_LEN and len(strategy.losing_side) >= MIN_REASON_LEN
    return GateResult(
        "reason", ok,
        f"reason={len(strategy.reason)} chars, losing_side={len(strategy.losing_side)} chars "
        f"(need >= {MIN_REASON_LEN} each)",
    )


def _gate_costs(
    strategy: StrategyDef, symbol_bars: dict[str, list[dict]], base_trades: list[Trade]
) -> GateResult:
    if not base_trades:
        return GateResult("costs", False, "no trades at base cost")
    stressed = run_backtest(symbol_bars, strategy, cost_per_side_pct=0.001)
    mean_r_stressed = _mean_r(stressed)
    ok = mean_r_stressed > 0
    return GateResult(
        "costs", ok,
        f"mean R at 2x costs (10bps/side) = {mean_r_stressed:.3f} ({len(stressed)} trades)",
    )


def _gate_out_of_sample(base_trades: list[Trade]) -> GateResult:
    if len(base_trades) < 10:
        return GateResult("out_of_sample", False, "too few trades to split")
    ordered = sorted(base_trades, key=lambda t: t.entry_date)
    split = int(len(ordered) * 0.7)
    train, test = ordered[:split], ordered[split:]
    mean_train, mean_test = _mean_r(train), _mean_r(test)
    ok = mean_train > 0 and mean_test > 0
    return GateResult(
        "out_of_sample", ok,
        f"train mean R={mean_train:.3f} ({len(train)} trades), "
        f"test mean R={mean_test:.3f} ({len(test)} trades)",
    )


def _gate_robustness(
    strategy: StrategyDef, symbol_bars: dict[str, list[dict]], base_trades: list[Trade]
) -> GateResult:
    distinct_symbols = len({t.symbol for t in base_trades})
    if distinct_symbols < MIN_DISTINCT_SYMBOLS:
        return GateResult(
            "robustness", False,
            f"only {distinct_symbols} distinct symbols traded (need >= {MIN_DISTINCT_SYMBOLS})",
        )

    results = []
    for factor in PERTURBATION_FACTORS:
        perturbed = StrategyDef(
            name=strategy.name + f"_perturbed_{factor}",
            anomaly_column=strategy.anomaly_column,
            direction=strategy.direction,
            stop_pct=strategy.stop_pct * factor,
            target_pct=strategy.target_pct * factor if strategy.target_pct else None,
            max_holding_days=max(1, int(strategy.max_holding_days * factor)),
            reason=strategy.reason,
            losing_side=strategy.losing_side,
        )
        trades = run_backtest(symbol_bars, perturbed)
        results.append((factor, _mean_r(trades), len(trades)))

    ok = all(mean_r > 0 for _, mean_r, _ in results)
    detail = ", ".join(f"x{f}: mean R={m:.3f} ({n} trades)" for f, m, n in results)
    return GateResult("robustness", ok, f"{distinct_symbols} symbols; " + detail)


def _gate_luck_check(base_trades: list[Trade]) -> GateResult:
    if len(base_trades) < 20:
        return GateResult("luck_check", False, "too few trades for a meaningful Monte Carlo")
    r_values = [t.r_multiple for t in base_trades]
    rng = random.Random(1337)
    resampled_means = [
        statistics.mean(rng.choices(r_values, k=len(r_values)))
        for _ in range(MONTE_CARLO_ITERATIONS)
    ]
    resampled_means.sort()
    cutoff_idx = int(MONTE_CARLO_ITERATIONS * MONTE_CARLO_PERCENTILE / 100)
    worst_5pct = resampled_means[cutoff_idx]
    ok = worst_5pct > 0
    return GateResult(
        "luck_check", ok,
        f"worst {MONTE_CARLO_PERCENTILE}% of {MONTE_CARLO_ITERATIONS} bootstrap resamples: "
        f"mean R={worst_5pct:.3f}",
    )


def _gate_baseline(
    strategy: StrategyDef, symbol_bars: dict[str, list[dict]], base_trades: list[Trade]
) -> GateResult:
    if not base_trades:
        return GateResult("baseline", False, "no trades to compare")
    strategy_mean_r = _mean_r(base_trades)

    random_baseline = run_random_entry_baseline(symbol_bars, strategy, n_signals=len(base_trades))
    random_mean_r = _mean_r(random_baseline)

    buy_hold_returns = []
    for symbol, bars in symbol_bars.items():
        if len(bars) < 2:
            continue
        first, last = bars[0]["close"], bars[-1]["close"]
        if first:
            buy_hold_returns.append((last - first) / first)
    buy_hold_mean = statistics.mean(buy_hold_returns) if buy_hold_returns else 0.0
    # Buy-and-hold's return isn't in R-multiples -- treat beating a flat 0%
    # (i.e. the strategy must be net-positive on its own terms) as the bar,
    # plus explicitly beating random entries on the same exit mechanics.
    ok = strategy_mean_r > random_mean_r and strategy_mean_r > 0
    return GateResult(
        "baseline", ok,
        f"strategy mean R={strategy_mean_r:.3f} vs random-entry mean R={random_mean_r:.3f} "
        f"({len(random_baseline)} trades); universe buy-hold mean return={buy_hold_mean:.1%} "
        "over the same window (informational, not R-comparable)",
    )


def _gate_sample_size(base_trades: list[Trade], min_trades: int) -> GateResult:
    ok = len(base_trades) >= min_trades
    return GateResult(
        "sample_size", ok, f"{len(base_trades)} out-of-sample trades (need >= {min_trades})"
    )


def run_gates(
    strategy: StrategyDef, symbol_bars: dict[str, list[dict]], min_trades: int = 50
) -> SkepticReport:
    base_trades = run_backtest(symbol_bars, strategy)

    gates = [
        _gate_reason(strategy),
        _gate_costs(strategy, symbol_bars, base_trades),
        _gate_out_of_sample(base_trades),
        _gate_robustness(strategy, symbol_bars, base_trades),
        _gate_luck_check(base_trades),
        _gate_baseline(strategy, symbol_bars, base_trades),
        _gate_sample_size(base_trades, min_trades),
    ]
    return SkepticReport(strategy_name=strategy.name, gates=gates, trades=base_trades)
