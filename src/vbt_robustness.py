"""Vectorized parameter-grid stress test for the Robustness gate
(src/skeptic.py), using vectorbt to simulate many stop/target combinations
in one call per symbol instead of the previous 2-point perturbation check.

Scope note: this sweep omits the strategy's max_holding_days time-exit
backstop -- trades ride to their stop or target only. That keeps the whole
grid vectorizable in a single call per symbol. It's an honest stress test
of stop/target sensitivity specifically, not a byte-for-byte replay of the
production exit mechanics -- backtest.py's event-loop simulation (exit-
complete: stop, target, OR time, whichever first) is what every other gate,
the shortlist, and execution actually use. Verified against known inputs
before relying on it: intrabar stop/target fills using open/high/low/close
match backtest.py's own fill logic (exit at the stop/target price, not the
bar's close, when a bar's high/low spans it), and short-direction entries
correctly flip the stop/target sides.
"""

import numpy as np
import pandas as pd
import vectorbt as vbt

from .ideas import StrategyDef
from .scout import bars_to_df, compute_signals

STOP_FACTORS = [0.6, 0.8, 1.0, 1.2, 1.4]
TARGET_FACTORS = [0.6, 0.8, 1.0, 1.2, 1.4]
COST_PER_SIDE_PCT = 0.0005
MIN_HISTORY_DAYS = 260


def _entries_for_symbol(df: pd.DataFrame, anomaly_column: str) -> pd.Series:
    if anomaly_column not in df.columns:
        return pd.Series(False, index=df.index)
    signal = df[anomaly_column].fillna(False).astype(bool)
    # Same "entries one bar late" rule as backtest.py: the signal fires on
    # bar i's close, the entry happens on bar i+1.
    return signal.shift(1, fill_value=False)


def run_grid(
    strategy: StrategyDef,
    symbol_bars: dict[str, list[dict]],
    stop_factors: list[float] = STOP_FACTORS,
    target_factors: list[float] = TARGET_FACTORS,
    min_history_days: int = MIN_HISTORY_DAYS,
) -> dict:
    """Runs the full stop_factor x target_factor grid (applied as
    multipliers on the strategy's own stop_pct/target_pct) across every
    symbol in symbol_bars, aggregating mean R-multiple and trade count per
    combo. Returns {"combos": [(stop_pct, target_pct), ...], "mean_r":
    [float | None, ...], "trade_counts": [int, ...]}, one entry per combo,
    in the same order as the combos list.
    """
    if strategy.target_pct is None:
        combos = [(strategy.stop_pct * sf, None) for sf in stop_factors]
        tp_arr = None
    else:
        combos = [
            (strategy.stop_pct * sf, strategy.target_pct * tf)
            for sf in stop_factors
            for tf in target_factors
        ]
        tp_arr = np.array([c[1] for c in combos]).reshape(1, -1)
    stop_arr = np.array([c[0] for c in combos]).reshape(1, -1)
    n_combos = len(combos)

    r_values_by_combo: list[list[float]] = [[] for _ in range(n_combos)]
    direction = "shortonly" if strategy.direction == "short" else "longonly"

    for bars in symbol_bars.values():
        if len(bars) < min_history_days:
            continue
        df = compute_signals(bars_to_df(bars))
        entries = _entries_for_symbol(df, strategy.anomaly_column)
        if not entries.any():
            continue

        try:
            pf = vbt.Portfolio.from_signals(
                df["close"], entries=entries,
                open=df["open"], high=df["high"], low=df["low"],
                price=df["open"], direction=direction,
                sl_stop=stop_arr, tp_stop=tp_arr,
                fees=COST_PER_SIDE_PCT, init_cash=100_000, freq="D",
            )
        except Exception:
            continue

        trades = pf.trades.records_readable
        if trades.empty:
            continue
        for combo_idx in range(n_combos):
            combo_trades = trades[trades["Column"] == combo_idx]
            if combo_trades.empty:
                continue
            stop_pct = combos[combo_idx][0]
            risk_dollars = (
                combo_trades["Size"] * combo_trades["Avg Entry Price"] * stop_pct
            )
            r_values = (combo_trades["PnL"] / risk_dollars).tolist()
            r_values_by_combo[combo_idx].extend(r_values)

    mean_r = [float(np.mean(v)) if v else None for v in r_values_by_combo]
    trade_counts = [len(v) for v in r_values_by_combo]
    return {"combos": combos, "mean_r": mean_r, "trade_counts": trade_counts}
