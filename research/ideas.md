# Ideas under test

## volume_spike_breakout_continuation (long)
- Trigger: volume_spike
- Stop: 5.0%, Target: 10.0%, max hold: 10d
- Reason: A volume surge well above a name's own 20-day average often marks the start of institutional accumulation or a real news-driven re-rating, not noise -- the move that created the spike tends to continue for days, not reverse immediately.
- Losing side: Short-term traders who fade the spike expecting an immediate blow-off top, and existing holders who sell into the volume out of habit rather than any new information, are on the other side of a continuation that actually follows through.

## gap_down_mean_reversion (long)
- Trigger: gap_down
- Stop: 4.0%, Target: 5.0%, max hold: 5d
- Reason: A single-day gap down of 3%+ with no accompanying volume-spike anomaly (i.e. not a volume-confirmed breakdown) is more often panic or forced selling (stop runs, index rebalancing, thin pre-market liquidity) than a genuine repricing, and tends to partially retrace within a few sessions.
- Losing side: Panic sellers who dump into the gap without re-checking whether the news justifies the full move are the losing side when the price mean-reverts back toward the prior close.

## volatility_expansion_short_continuation (short)
- Trigger: volatility_expansion_down
- Stop: 4.0%, Target: 6.0%, max hold: 5d
- Reason: A daily range more than 2.5x the recent 20-day average range, on a down day, often marks the start of a liquidation cascade or a genuine regime shift (bad earnings, guidance cut) rather than a one-day shock that's immediately over -- these tend to see follow-through selling over the next few sessions.
- Losing side: Dip-buyers who step in on day one assuming the worst is already priced in are the losing side when the selling continues; they're catching a falling knife too early.
