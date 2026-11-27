# BriggsTrading Playbook

## Mandate (one line)
Scan liquid US stocks nightly for statistically real, cost-surviving edges, paper-trade
only the ones that survive adversarial testing, and say "no trade" when nothing clears
the bar.

## What we trade
- **Markets:** US equities only, via Alpaca (paper account). No crypto, no options.
- **Universe:** a fixed list of ~150 liquid, large-cap US stocks (`src/universe.py`).
  Not the literal "whole market" -- names outside this list are never scanned, which
  keeps data volume and backtest time bounded. Revisit the list only deliberately,
  not as a reaction to one bad week.
- **Timeframe:** swing trades. Entries and exits on daily bars, typical holding period
  days to a couple of weeks. No intraday/scalping.
- **Setups per morning:** 1-2 new entries max, whatever the shortlist says -- never more,
  even if more look tempting.

## Paper capital and risk (independent of the strategy's own signal)
- **Paper capital:** whatever the Alpaca paper account currently holds (no fixed
  number hardcoded -- `risk_guard.py` reads live equity each run).
- **Risk per trade:** 1% of equity, sized from the stop distance (not a flat
  dollar amount) -- see EV math below.
- **Daily loss limit:** -3% realized on the day halts all new entries until the
  next UTC day. Existing positions and their stops are untouched.
- **Drawdown kill switch:** -15% below the account's 3-month equity peak halts
  everything -- new entries AND it stops touching existing positions too, until
  a human looks at it.
- **Max leverage:** 2x. **Shorting:** allowed, same risk rules as longs.
- **Correlation cap:** total open risk across positions that tend to move
  together counts as one bet; total open risk across the whole book stays
  under 3% of equity.

## Time zone
US Eastern, matching market hours and the official CPI/FOMC release calendar.
The "morning brief" means before the 9:30am ET open; the nightly research run
means after the 4pm ET close.

## The pipeline (see src/research_pipeline.py and src/execution.py)
1. **Scout** (after close): scan the universe for anomalies -- volume spikes,
   gaps, volatility expansion, momentum extremes vs. each name's own history.
2. **Ideas**: turn each anomaly type into an exact, mechanical strategy (entry
   trigger, stop, target/exit rule, holding period, and who the losing side of
   the trade is and why).
3. **Quant**: backtest each idea over real historical daily bars with modeled
   commission, spread and slippage -- no frictionless backtests.
4. **Skeptic**: every idea must pass all 7 gates (see src/skeptic.py) --
   Reason, Costs (2x stress), Out-of-sample, Robustness, Luck check (Monte
   Carlo), Baseline comparison, Sample size. Most ideas die here. That's the
   system working, not failing.
5. **Rank**: survivors are scored 0-10 (Evidence/Fit/Trade quality). Only
   scores of 8+ make the morning shortlist.
6. **Execute** (before open): read the shortlist, size each entry by risk
   (1% of equity / stop distance, capped by a fraction of Kelly), check the
   independent risk guard (daily loss, drawdown, exposure, concentration),
   and submit paper orders via Alpaca. No shortlist survivors some mornings
   is a valid, expected outcome -- "no trade" is the answer when nothing
   clears the bar.

## Where everything lives
- `playbook.md` -- this file.
- `research/scan.md` -- last night's raw anomalies.
- `research/ideas.md` -- hypotheses waiting on/that went through backtest.
- `research/killed.md` -- every idea that failed a gate, and which one, so it
  isn't re-tried blind.
- `research/shortlist.md` -- today's survivors, scored, with the evidence.
- `research/strategies/` -- one file per strategy definition (mechanical rules).
- `research/backtests/` -- one JSON result per backtest run, with gate results.
- `data/trades.csv` -- every paper trade: entry/exit, size, stop, strategy,
  predicted EV/R, realized R, so predicted vs. realized can be compared later.
- `data/decisions_log.jsonl` -- audit log of every decision (trade or skip),
  shown on the dashboard.

## Golden rules
- No reason, no test.
- No passing all 7 gates, no setup.
- No score of 8+, no entry.
- No stop in the system, no trade.
- No positive EV after costs, no entry.
- Real money is never part of this playbook -- this is paper trading only;
  going live is a separate, deliberate, future decision.
