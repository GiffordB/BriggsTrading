# BriggsTrading

Mirrors publicly disclosed U.S. Congress stock trades (STOCK Act filings, via
the [Quiver Quantitative API](https://api.quiverquant.com/pricing/)) into an
[Alpaca](https://alpaca.markets) account. **Defaults to Alpaca paper trading
and dry-run mode** -- it will not place a real order until you deliberately
turn both of those off.

## Important context

- Congress members must disclose trades within 30-45 days of making them.
  This bot is not fast money and cannot front-run anything -- by the time a
  trade is disclosed, the market has usually already reacted to whatever the
  member knew.
- This is not financial advice, and following politicians' trades is not a
  guaranteed strategy. Past disclosed performance of any individual member is
  not predictive.
- You are responsible for your own brokerage account, API keys, and any money
  this places at risk if you ever turn on live trading.

## Setup

1. `pip install -r requirements.txt`
2. Get a Quiver Quantitative API token (Hobbyist tier, $30/mo, includes
   Congress Trading data): https://api.quiverquant.com/pricing/
3. Get Alpaca paper trading API keys (free): https://app.alpaca.markets/
4. `cp .env.example .env` and fill in `QUIVER_API_TOKEN`, `ALPACA_API_KEY`,
   `ALPACA_SECRET_KEY`.
5. Leave `ALPACA_PAPER=true` and `DRY_RUN=true` for your first runs.

## Running it

```
python -m src.main
```

With `DRY_RUN=true` this fetches recent disclosures, applies the strategy
filters, computes what it *would* order, and logs it -- no orders are
submitted, but disclosures are still marked as "seen" so you don't get a wall
of duplicate log lines every run.

Once you're happy with what it's logging, set `DRY_RUN=false` to actually
submit orders to your **paper** account and watch it for a while before ever
considering live money.

### Going live (real money)

Only if you're sure: set `ALPACA_PAPER=false` **and**
`CONFIRM_LIVE_TRADING=I-UNDERSTAND-THIS-IS-REAL-MONEY` in `.env`. The bot
refuses to start against a live account without that exact confirmation
string set on purpose -- there's no accidental path into live trading.

## Strategy knobs (all in `.env`)

| Setting | What it does |
|---|---|
| `POSITION_SIZE_PCT` | % of your account equity to allocate per mirrored trade |
| `MAX_NOTIONAL_PER_TRADE` | Hard dollar cap per order, regardless of `POSITION_SIZE_PCT` |
| `MAX_NOTIONAL_PER_RUN` | Hard dollar cap on total new orders in one run |
| `MIN_TRADE_AMOUNT` | Ignore disclosures below this dollar amount (disclosures are ranges; the low end is used) |
| `MIRROR_TRANSACTION_TYPES` | Which disclosure types to act on (`Purchase`, `Sale (Full)`, etc.) |
| `FOLLOWED_MEMBERS` | Optional allowlist of specific members to mirror; blank = everyone |
| `LOOKBACK_DAYS` | How far back to look for new disclosures each run |

Sell disclosures only ever close a position this bot already opened for you
-- it will never short a stock or sell something you hold for unrelated
reasons.

## Risk guard (independent of the strategy above)

Before any order is submitted, a separate check in `src/risk_guard.py` runs
-- deliberately independent of the strategy's own filters, so a bug in
`strategy.py` can't bypass it. Configured in `.env`:

| Setting | What it does |
|---|---|
| `TRADING_HALTED` | Manual kill switch -- set `true` to immediately stop all new orders |
| `MAX_DRAWDOWN_PCT` | Auto-halts trading if equity has fallen this much below its 3-month peak (from Alpaca's own portfolio history) |
| `MAX_POSITION_CONCENTRATION_PCT` | Blocks a buy that would push any single position above this fraction of account equity |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | Blocks new buys once total position value reaches this fraction of account equity |

When halted, the bot still evaluates and logs every disclosure (so you can
see what it *would* have done), it just skips submitting orders -- and
doesn't mark those disclosures as "seen", so they're retried automatically
once the halt clears.

## Audit log

Every disclosure the bot evaluates -- mirrored or not -- is logged with its
outcome and reason to `data/decisions_log.jsonl`, which the GitHub Actions
workflow commits back to the repo after each run (using GitHub's own
built-in token, no extra secrets needed). The dashboard reads this file
straight from GitHub to show a live audit trail alongside the account data.

## Performance metrics

The dashboard also shows Sharpe ratio, max drawdown, and CAGR, computed
directly from Alpaca's own portfolio history endpoint (`get_portfolio_history`)
-- no separate tracking database needed. "Open position win rate" is a
simplified proxy (% of currently open positions with positive unrealized
P/L), not a rigorous realized-P/L trade ledger.

## Backtesting

Before trusting a filter change, sanity-check it against history:
```
python -m src.backtest
```
This simulates the same strategy filters against Quiver's historical
disclosure data and Alpaca's free historical daily bars over the last
`BACKTEST_DAYS` (default 180), reporting total return, CAGR, max drawdown,
Sharpe ratio, and win rate on closed trades. It's a real but simplified
simulation -- fills happen at each disclosure's filed-date close price, with
no slippage or commission modeling, and equity is only marked-to-market at
trade-event dates rather than daily. Treat it as a sanity check, not a
guarantee of future performance.

## Scheduling

Congress disclosure data doesn't update faster than daily, so there's no
value running this more than once a day. Two options:

- **Cron on a machine you control** (recommended -- simplest state handling):
  `0 22 * * 1-5 cd /path/to/BriggsTrading && python -m src.main`
- **GitHub Actions** (`.github/workflows/run-bot.yml`, included, disabled
  until you add repo secrets): works, but GitHub's runners are ephemeral so
  the workflow stores `seen_trades.db` as a build artifact and restores it
  each run. That's a bit more fragile than a persistent machine -- if you
  have any server or Raspberry Pi lying around, cron there is more robust.
  Add `QUIVER_API_TOKEN`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` as repo
  secrets, and `ALPACA_PAPER` / `DRY_RUN` / `CONFIRM_LIVE_TRADING` /
  `TRADING_HALTED` / `MAX_DRAWDOWN_PCT` / `MAX_POSITION_CONCENTRATION_PCT` /
  `MAX_PORTFOLIO_EXPOSURE_PCT` as repo variables, to use it. The workflow also
  needs `contents: write` permission (already set in the file) so it can
  commit the audit log back to the repo after each run.

## Dashboard

`dashboard/` is a small Flask app that shows account equity, open positions,
recent orders this bot has placed, and the raw disclosure feed -- polling
every 30 seconds. It reads live from Alpaca and Quiver; it doesn't need the
bot's cron job to be running to show current state.

Run it locally:
```
python -m flask --app dashboard.app run
```
then open http://127.0.0.1:5000.

Deploy it on Render using the included `render.yaml`: create a new Blueprint
from this repo in the Render dashboard, and set `QUIVER_API_TOKEN`,
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY` as secret env vars there (they're marked
`sync: false` so Render prompts for them rather than storing them in the repo).

## Known limitations

- The Quiver API response shape has changed before; if `src/quiver_client.py`
  starts erroring, check https://api.quiverquant.com/docs/ for the current
  field names on `/beta/live/congresstrading`.
- Orders are simple market orders sized as a fraction of account equity --
  there's no stop-loss, take-profit, or portfolio rebalancing logic.
- Only mirrors single stocks Alpaca can trade; options, bonds, and other
  disclosed asset types are skipped.
