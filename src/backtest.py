"""Quant: simulates a StrategyDef over real historical daily bars, with
commission/spread/slippage modeled as a flat per-side cost, and returns the
resulting trade list. No lookahead: entry is always the bar AFTER the signal
bar (the doc's "entries one bar late"), and only that entry bar's own
open/high/low/close/volume (not anything later) decides fills.
"""

from dataclasses import dataclass

import pandas as pd

from .ideas import StrategyDef
from .scout import bars_to_df, compute_signals

# Alpaca charges no commission on US equities; this models spread + slippage
# only. 5bps per side is a conservative-but-plausible estimate for the
# liquid, large-cap universe this trades -- see playbook.md.
DEFAULT_COST_PER_SIDE_PCT = 0.0005


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: str
    signal_date: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str  # "stop", "target", or "time"
    r_multiple: float
    return_pct: float


def _simulate_one_symbol(
    symbol: str, df: pd.DataFrame, strategy: StrategyDef, cost_per_side_pct: float
) -> list[Trade]:
    if strategy.anomaly_column not in df.columns:
        return []

    trades: list[Trade] = []
    n = len(df)
    blocked_until = -1  # no overlapping trades on the same symbol

    for i in range(n - 1):
        if i <= blocked_until:
            continue
        signal_val = df.iloc[i].get(strategy.anomaly_column)
        if not bool(signal_val) or pd.isna(signal_val):
            continue

        entry_idx = i + 1
        entry_row = df.iloc[entry_idx]
        entry_open = entry_row["open"]
        if pd.isna(entry_open) or entry_open <= 0:
            continue

        is_long = strategy.direction == "long"
        slip = entry_open * cost_per_side_pct
        entry_price = entry_open + slip if is_long else entry_open - slip

        stop_price = (
            entry_price * (1 - strategy.stop_pct)
            if is_long
            else entry_price * (1 + strategy.stop_pct)
        )
        target_price = None
        if strategy.target_pct:
            target_price = (
                entry_price * (1 + strategy.target_pct)
                if is_long
                else entry_price * (1 - strategy.target_pct)
            )

        max_idx = min(entry_idx + strategy.max_holding_days, n - 1)
        exit_idx, exit_price_raw, exit_reason = None, None, "time"
        for j in range(entry_idx, max_idx + 1):
            row = df.iloc[j]
            if is_long:
                if row["low"] <= stop_price:
                    exit_idx, exit_price_raw, exit_reason = j, stop_price, "stop"
                    break
                if target_price and row["high"] >= target_price:
                    exit_idx, exit_price_raw, exit_reason = j, target_price, "target"
                    break
            else:
                if row["high"] >= stop_price:
                    exit_idx, exit_price_raw, exit_reason = j, stop_price, "stop"
                    break
                if target_price and row["low"] <= target_price:
                    exit_idx, exit_price_raw, exit_reason = j, target_price, "target"
                    break

        if exit_idx is None:
            exit_idx = max_idx
            exit_price_raw = df.iloc[exit_idx]["close"]
            exit_reason = "time"

        slip_exit = exit_price_raw * cost_per_side_pct
        exit_price = exit_price_raw - slip_exit if is_long else exit_price_raw + slip_exit

        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            continue
        raw_pnl = (exit_price - entry_price) if is_long else (entry_price - exit_price)

        trades.append(
            Trade(
                symbol=symbol,
                direction=strategy.direction,
                signal_date=str(df.iloc[i]["date"].date()),
                entry_date=str(entry_row["date"].date()),
                entry_price=float(entry_price),
                exit_date=str(df.iloc[exit_idx]["date"].date()),
                exit_price=float(exit_price),
                exit_reason=exit_reason,
                r_multiple=float(raw_pnl / risk_per_share),
                return_pct=float(raw_pnl / entry_price),
            )
        )
        blocked_until = exit_idx

    return trades


def run_backtest(
    symbol_bars: dict[str, list[dict]],
    strategy: StrategyDef,
    cost_per_side_pct: float = DEFAULT_COST_PER_SIDE_PCT,
    min_history_days: int = 260,
) -> list[Trade]:
    all_trades: list[Trade] = []
    for symbol, bars in symbol_bars.items():
        if len(bars) < min_history_days:
            continue
        df = compute_signals(bars_to_df(bars))
        all_trades.extend(_simulate_one_symbol(symbol, df, strategy, cost_per_side_pct))
    return all_trades


def run_random_entry_baseline(
    symbol_bars: dict[str, list[dict]],
    strategy: StrategyDef,
    n_signals: int,
    cost_per_side_pct: float = DEFAULT_COST_PER_SIDE_PCT,
    seed: int = 42,
) -> list[Trade]:
    """Same stop/target/time-exit mechanics as the real strategy, but entries
    are picked at random dates instead of on the anomaly signal -- the
    'baseline' gate's random-entry comparison (src/skeptic.py). Draws
    `n_signals` random (symbol, index) entry points so the baseline is
    judged on a comparable sample size."""
    import random

    rng = random.Random(seed)
    eligible: list[tuple[str, pd.DataFrame, int]] = []
    for symbol, bars in symbol_bars.items():
        if len(bars) < 260:
            continue
        df = compute_signals(bars_to_df(bars))
        for i in range(len(df) - strategy.max_holding_days - 1):
            eligible.append((symbol, df, i))

    if not eligible:
        return []

    sample = rng.sample(eligible, k=min(n_signals, len(eligible)))
    fake_strategy = StrategyDef(
        name=strategy.name + "_random_baseline",
        anomaly_column="__always_true__",
        direction=strategy.direction,
        stop_pct=strategy.stop_pct,
        target_pct=strategy.target_pct,
        max_holding_days=strategy.max_holding_days,
        reason="baseline",
        losing_side="baseline",
    )

    trades: list[Trade] = []
    for symbol, df, i in sample:
        marked = df.copy()
        marked["__always_true__"] = False
        marked.iloc[i, marked.columns.get_loc("__always_true__")] = True
        trades.extend(_simulate_one_symbol(symbol, marked, fake_strategy, cost_per_side_pct))
    return trades
