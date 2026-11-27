"""Scout: turns raw daily OHLCV bars into anomaly signals.

Every column added here is computed using only data available as of its own
row's close (rolling stats are shifted by one day before comparing against
today) -- "signals use closed bars only," per playbook.md. These same signal
columns are used both for tonight's human-readable scan (what's anomalous
right now) and for backtesting (how often has this anomaly type occurred,
historically, and what happened after).
"""

import pandas as pd

ANOMALY_COLUMNS = [
    "volume_spike",
    "gap_down",
    "volatility_expansion_down",
]


def bars_to_df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["avg_volume_20"] = out["volume"].rolling(20).mean().shift(1)
    out["volume_spike"] = out["volume"] > 3.0 * out["avg_volume_20"]

    out["prev_close"] = out["close"].shift(1)
    out["gap_pct"] = (out["open"] - out["prev_close"]) / out["prev_close"]
    out["gap_down"] = out["gap_pct"] < -0.03

    out["daily_range_pct"] = (out["high"] - out["low"]) / out["prev_close"]
    out["range_avg_20"] = out["daily_range_pct"].rolling(20).mean().shift(1)
    out["down_day"] = out["close"] < out["prev_close"]
    out["volatility_expansion_down"] = (
        (out["daily_range_pct"] > 2.5 * out["range_avg_20"]) & out["down_day"]
    )

    return out


def scan_tonight(symbol_bars: dict[str, list[dict]]) -> list[dict]:
    """What's anomalous as of the most recent bar, per symbol -- the raw
    material for research/scan.md. Not used for sizing or trading decisions
    on its own; see ideas.py / backtest.py for the actual, testable rules."""
    tonight = []
    for symbol, bars in sorted(symbol_bars.items()):
        if len(bars) < 260:
            continue
        df = compute_signals(bars_to_df(bars))
        last = df.iloc[-1]
        for col in ANOMALY_COLUMNS:
            val = last.get(col)
            if bool(val) and not pd.isna(val):
                tonight.append(
                    {"symbol": symbol, "date": str(last["date"].date()), "anomaly": col}
                )
    return tonight
