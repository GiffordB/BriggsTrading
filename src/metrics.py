from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class PerformanceMetrics:
    sharpe_ratio: float | None
    max_drawdown_pct: float
    cagr_pct: float | None
    open_position_win_rate_pct: float | None
    num_data_points: int


def compute_metrics(
    portfolio_history: list[dict], positions: list[dict]
) -> PerformanceMetrics:
    """Computes standard performance metrics from Alpaca's own portfolio history --
    no separately-tracked equity store needed. Returns None for stats that need more
    history than is currently available (e.g. a brand new account)."""
    equities = [h["equity"] for h in portfolio_history if h.get("equity") is not None]

    max_drawdown_pct = 0.0
    peak = None
    for equity in equities:
        peak = equity if peak is None else max(peak, equity)
        if peak:
            max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak)

    sharpe_ratio = None
    if len(equities) >= 3:
        daily_returns = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
            if equities[i - 1]
        ]
        if len(daily_returns) >= 2:
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_return) ** 2 for r in daily_returns) / (
                len(daily_returns) - 1
            )
            std_dev = variance**0.5
            if std_dev > 0:
                sharpe_ratio = (mean_return / std_dev) * (252**0.5)

    cagr_pct = None
    timestamps = [
        h["timestamp"] for h in portfolio_history if h.get("equity") is not None
    ]
    if len(equities) >= 2 and equities[0] > 0 and len(timestamps) == len(equities):
        start_dt = datetime.fromtimestamp(timestamps[0], tz=timezone.utc)
        end_dt = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc)
        days = max((end_dt - start_dt).days, 1)
        total_return = equities[-1] / equities[0]
        cagr_pct = (total_return ** (365 / days) - 1) * 100

    open_position_win_rate_pct = None
    if positions:
        winners = sum(1 for p in positions if p["unrealized_pl"] > 0)
        open_position_win_rate_pct = (winners / len(positions)) * 100

    return PerformanceMetrics(
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct * 100,
        cagr_pct=cagr_pct,
        open_position_win_rate_pct=open_position_win_rate_pct,
        num_data_points=len(equities),
    )
