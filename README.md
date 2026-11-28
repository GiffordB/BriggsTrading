# BriggsTrading

A systematic research desk, not a signal-mirroring bot: every weeknight it
scans a fixed universe of liquid US stocks for statistical anomalies, turns
each anomaly type into an exact mechanical strategy, backtests it against
years of real daily bars with realistic costs, and tries hard to kill it
before it ever reaches a real order. Only strategies that survive all 7
adversarial gates and score high enough make the next morning's shortlist.
Most ideas die in testing -- that's the system working, not failing. See
`playbook.md` for the full mandate.

**Defaults to Alpaca paper trading and dry-run mode** -- it will not place a
real order until you deliberately turn both of those off, and "real money"
isn't part of this system at all right now; going live would be a separate,
future, deliberate decision.

## The pipeline

```
Nightly (after close)              Morning (before open)
--------------------               ----------------------
Scout: scan for anomalies    -->   Read last night's shortlist
Ideas: mechanical strategies -->   Cross-reference with tonight's signals
Quant: backtest w/ real costs -->  Size via risk % + Kelly cap
Skeptic: 7 gates must all pass --> Risk guard (daily loss/drawdown/exposure)
Rank: score 0-10, shortlist  -->   Submit bracket paper orders via Alpaca
```

- `src/universe.py` -- the fixed ~150-name liquid US stock universe.
- `src/scout.py` -- anomaly detectors (volume spike, gap down, volatility
  expansion), computed with no lookahead.
- `src/ideas.py` -- each anomaly's exact, mechanical strategy definition
  (entry, stop, target, max holding period, and who the losing side is).
- `src/backtest.py` -- vectorized backtester with modeled spread/slippage.
- `src/skeptic.py` -- the 7 gates (Reason, Costs, Out-of-sample, Robustness,
  Luck check / Monte Carlo, Baseline comparison, Sample size).
- `src/rank.py` -- scores survivors 0-10, writes `research/shortlist.md` /
  `research/killed.md` / `research/backtests/*.json`.
- `src/research_pipeline.py` -- nightly orchestration (`python -m src.main
  research`).
- `src/execution.py` -- morning orchestration (`python -m src.main
  execute`): manages time-based exits, checks the risk guard, sizes and
  submits new bracket-order entries.
- `src/risk_guard.py` -- independent pre-trade check (daily loss limit,
  drawdown kill switch, exposure/concentration caps) that a bug anywhere
  else in the pipeline can't bypass.

## Setup

1. `pip install -r requirements.txt`
2. Get Alpaca paper trading API keys (free): https://app.alpaca.markets/
3. `cp .env.example .env` and fill in `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.
4. Leave `ALPACA_PAPER=true` and `DRY_RUN=true` for your first runs.

## Running it

```
python -m src.main research   # nightly: scan, backtest, gate, rank, shortlist
python -m src.main execute    # morning: act on last night's shortlist
```

`research` has no `DRY_RUN` concept -- it only reads market data and writes
files under `research/`, never touches the broker's order endpoint.

`execute` with `DRY_RUN=true` reads the shortlist, applies risk sizing, and
logs what it *would* order -- no orders are submitted. Set `DRY_RUN=false`
to actually submit bracket orders to your **paper** account and watch it
for a while before ever considering live money.

Run `research` after market close and `execute` before the next open --
`.github/workflows/research.yml` and `.github/workflows/execute.yml` do
this automatically; see "Scheduling" below.

### Going live (real money)

Only if you're sure: set `ALPACA_PAPER=false` **and**
`CONFIRM_LIVE_TRADING=I-UNDERSTAND-THIS-IS-REAL-MONEY` in `.env`. The bot
refuses to start against a live account without that exact confirmation
string set on purpose -- there's no accidental path into live trading.

## How a trade is sized

Each shortlisted strategy carries its own backtested stop distance and
historical win rate / avg win / avg loss. A new entry is sized at
`RISK_PER_TRADE_PCT` of equity divided by the stop distance, capped by a
fraction of Kelly (`KELLY_FRACTION_CAP`) computed from that strategy's own
backtest, and capped again by `MAX_LEVERAGE`. Entries are submitted as
Alpaca bracket orders, so the stop-loss and take-profit are enforced by the
exchange itself, not by a bot that has to be running at the right moment --
only the strategy's max-holding-period time-exit needs a later run to find
the position still open and force-close it (`src/state.py` tracks this
across runs via a small SQLite file restored as a build artifact).

## Risk guard (independent of the pipeline above)

Before any new entry, `src/risk_guard.py` runs a check that never calls
into the strategy/scoring code, so a bug there can't bypass it:

| Setting | What it does |
|---|---|
| `TRADING_HALTED` | Manual kill switch -- blocks all new entries immediately |
| `DAILY_LOSS_LIMIT_PCT` | Blocks new entries (not existing positions) for the rest of the UTC day once today's P&L breaches this |
| `MAX_DRAWDOWN_PCT` | Halts **everything**, including managing existing positions, if equity has fallen this much below its 3-month peak |
| `MAX_POSITION_CONCENTRATION_PCT` | Blocks an entry that would push any single position above this fraction of equity |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | Blocks new entries once total position value reaches this fraction of equity |

## Audit log and trade log

Every decision (entry, skip, or "no trade today" and why) is logged to
`data/decisions_log.jsonl`. Every position that actually closes is logged
to `data/trades.csv` with its predicted R vs. realized outcome, so the
desk's own calibration (predicted vs. actual) can be checked over time.
Both are committed back to the repo by the GitHub Actions workflows using
GitHub's own built-in token -- no extra secrets needed. The dashboard reads
them straight from GitHub, including a one-click **Reverse** button on any
`enter` row that force-closes the position immediately.

## Scheduling

Two separate workflows, since research (after close) and execution (before
open) happen at very different times of day:

- `.github/workflows/research.yml` -- nightly, after close. Needs
  `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` as repo secrets (data access only,
  never places an order).
- `.github/workflows/execute.yml` -- each morning, before open. Needs the
  same secrets plus whatever risk/execution repo variables you want to
  override (all have sensible defaults baked in -- see `.env.example`).
  Stores `bot_state.db` as a build artifact across runs so time-based exits
  aren't lost if the runner is recycled.

Both need `contents: write` permission (already set) to commit their output
back to the repo. Given a full 5-year backtest across ~150 symbols runs
nightly, check your Actions usage if this repo is private -- a public repo
gets unlimited free minutes.

## Dashboard

`dashboard/` is a small Flask app showing account equity, open positions,
recent orders, last night's research shortlist, and the audit log --
polling every 30 seconds. It reads live from Alpaca plus the repo's
research/audit files; it doesn't need either GitHub Actions workflow to be
running to show current state.

Run it locally:
```
python -m flask --app dashboard.app run
```
then open http://127.0.0.1:5000.

Deploy it on Render using the included `render.yaml`: create a new
Blueprint from this repo in the Render dashboard, and set
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` as secret env vars there (they're
marked `sync: false` so Render prompts for them rather than storing them in
the repo).

### News alerts and manual close

The dashboard fetches recent news (via Alpaca's free News API) for whatever
tickers you currently hold, shown as a banner with a one-click **Sell**
button per headline that closes that position immediately at market. This
is a manual trigger only -- news never auto-closes anything on its own.

**This is a real trading action reachable from a web page, so the whole
dashboard requires a login before it works.** Set `DASHBOARD_USERNAME` and
`DASHBOARD_PASSWORD` -- without both set, the dashboard logs a loud warning
and runs with no login at all, which is only acceptable for local testing.

## Real Holdings tracker (optional)

A "Real Holdings (manual) vs. Paper Bot" section for tracking your own
actual investments (Schwab, a 401k, pre-IPO shares, whatever) side by side
with the paper bot's return %. Entries are added by hand from the
dashboard and persist as `data/real_holdings.json`, committed via the
GitHub Contents API (`GITHUB_PAT`, same mechanism as the audit log). Leave
`GITHUB_PAT` unset and the section just stays empty with add/edit/delete
disabled.

## Known limitations / scope this version deliberately cut

- **Universe is fixed, not "the whole market."** ~150 liquid large-caps,
  by design -- see `playbook.md`. Expanding it is a deliberate future
  change, not something the pipeline does on its own.
- **Only 3 candidate strategies exist so far** (`src/ideas.py`). The
  pipeline is built to test many more as they're added -- this is meant to
  be a living, growing set of ideas, most of which should keep dying in the
  Skeptic.
- **"Fit" scoring is a time-consistency check, not a full regime
  classifier.** The original design calls for tagging every trade with a
  trend/range/high-vol regime and matching it to today's regime; this
  version instead checks whether the edge held up in the most recent
  out-of-sample slice vs. the earlier slice, which is cheaper and catches
  the same "edge has decayed" failure mode without a full classifier.
- **Walk-forward is a single 70/30 chronological split**, not the full
  rolling 12-month-train/3-month-test-repeated design. Good enough to catch
  an edge that only existed in one era of data; not as rigorous as true
  rolling walk-forward.
- Only trades symbols Alpaca can trade (all of the fixed universe should
  qualify, but `is_tradable` is still checked before every entry).
- **The Robustness gate's parameter sweep (`src/vbt_robustness.py`, via
  vectorbt) omits the max_holding_days time-exit** -- trades in that sweep
  ride to their stop or target only, not the production "stop, target, or
  time, whichever first" rule. That keeps the whole 25-combo grid
  vectorizable in one call per symbol; it's a real, honest stress test of
  stop/target sensitivity specifically, just not a byte-for-byte replay of
  the production mechanics. Every other gate, the shortlist, and execution
  itself all use the exit-complete simulation in `src/backtest.py`.
