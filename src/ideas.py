"""Ideas: each anomaly type from scout.py gets turned into one exact,
mechanical strategy -- entry trigger, stop, target, max holding period, and
a plain statement of who the losing side of the trade is and why. This is
the "Reason" gate's raw material (src/skeptic.py) -- no idea reaches the
backtester without one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyDef:
    name: str
    anomaly_column: str  # which scout.py signal triggers this
    direction: str  # "long" or "short"
    stop_pct: float  # stop distance from entry, as a fraction of entry price
    target_pct: float | None  # take-profit distance; None = time-exit only
    max_holding_days: int
    reason: str  # why this should have edge
    losing_side: str  # who is on the other side of this trade, and why they lose


CANDIDATE_STRATEGIES: list[StrategyDef] = [
    StrategyDef(
        name="volume_spike_breakout_continuation",
        anomaly_column="volume_spike",
        direction="long",
        stop_pct=0.05,
        target_pct=0.10,
        max_holding_days=10,
        reason=(
            "A volume surge well above a name's own 20-day average often marks "
            "the start of institutional accumulation or a real news-driven "
            "re-rating, not noise -- the move that created the spike tends to "
            "continue for days, not reverse immediately."
        ),
        losing_side=(
            "Short-term traders who fade the spike expecting an immediate "
            "blow-off top, and existing holders who sell into the volume out "
            "of habit rather than any new information, are on the other side "
            "of a continuation that actually follows through."
        ),
    ),
    StrategyDef(
        name="gap_down_mean_reversion",
        anomaly_column="gap_down",
        direction="long",
        stop_pct=0.04,
        target_pct=0.05,
        max_holding_days=5,
        reason=(
            "A single-day gap down of 3%+ with no accompanying volume-spike "
            "anomaly (i.e. not a volume-confirmed breakdown) is more often "
            "panic or forced selling (stop runs, index rebalancing, thin "
            "pre-market liquidity) than a genuine repricing, and tends to "
            "partially retrace within a few sessions."
        ),
        losing_side=(
            "Panic sellers who dump into the gap without re-checking whether "
            "the news justifies the full move are the losing side when the "
            "price mean-reverts back toward the prior close."
        ),
    ),
    StrategyDef(
        name="volatility_expansion_short_continuation",
        anomaly_column="volatility_expansion_down",
        direction="short",
        stop_pct=0.04,
        target_pct=0.06,
        max_holding_days=5,
        reason=(
            "A daily range more than 2.5x the recent 20-day average range, on "
            "a down day, often marks the start of a liquidation cascade or a "
            "genuine regime shift (bad earnings, guidance cut) rather than a "
            "one-day shock that's immediately over -- these tend to see "
            "follow-through selling over the next few sessions."
        ),
        losing_side=(
            "Dip-buyers who step in on day one assuming the worst is already "
            "priced in are the losing side when the selling continues; "
            "they're catching a falling knife too early."
        ),
    ),
]
