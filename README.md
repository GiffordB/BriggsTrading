# BriggsTrading

Mirrors corporate insiders' own stock trades -- officers and directors filing
SEC Form 4 ("CEOs" in the broad sense of "the people actually running the
company"), sourced directly from [SEC EDGAR](https://www.sec.gov/edgar) (free,
no API key, no subscription) -- into an [Alpaca](https://alpaca.markets)
account. **Defaults to Alpaca paper trading and dry-run mode** -- it will not
place a real order until you deliberately turn both of those off.

## Important context

- A Form 4 is due within 2 business days of the trade -- much faster than a
  congressional disclosure's 30-45 day lag, but still after the fact. This
  bot is not fast money and cannot front-run anything.
- This scans **every** Form 4 filed market-wide each run, not a pre-selected
  watchlist -- see "How it finds trades" below for what that costs in run
  time and request volume.
- This is not financial advice, and mirroring insiders' trades is not a
  guaranteed strategy. Insiders sell for all kinds of reasons unrelated to
  their view of the stock (diversification, taxes, a planned 10b5-1 schedule)
  -- a sale is a much weaker signal than a purchase.
- You are responsible for your own brokerage account, API keys, and any money
  this places at risk if you ever turn on live trading.

## Setup

1. `pip install -r requirements.txt`
2. Get Alpaca paper trading API keys (free): https://app.alpaca.markets/
3. `cp .env.example .env` and fill in `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.
4. Leave `ALPACA_PAPER=true` and `DRY_RUN=true` for your first runs.

No SEC account or API key is needed -- `SEC_EDGAR_USER_AGENT` in `.env.example`
is just a descriptive string identifying this project to SEC's servers, per
their fair-access policy.

## How it finds trades

`src/sec_edgar_client.py` scans SEC's [daily filing
index](https://www.sec.gov/Archives/edgar/daily-index/) for every Form 4
filed in the last `LOOKBACK_DAYS` days, market-wide -- any company, not a
watchlist. For each one it fetches the actual filing XML to find genuine
open-market buys/sells (transaction codes `P`/`S`; option exercises, stock
grants, gifts, and tax withholding don't count).

This is a real cost worth knowing about: a single weekday has roughly 1,500
unique Form 4 filings market-wide, and there's no way to know which ones are
genuine open-market trades without fetching and parsing each one's XML. That's
a few thousand HTTP requests per day of lookback, which can take several
minutes to run and is most of why `LOOKBACK_DAYS` defaults to just 2 --
unlike a subscription feed, nothing here remembers "already scanned days", so
every run re-scans its full window from scratch. `MAX_INSIDER_FILINGS_PER_RUN`
is a safety cap against a multi-day backlog turning into an hours-long scan;
filings beyond it just aren't seen this run, not lost.

## Running it

```
python -m src.main
```

With `DRY_RUN=true` this scans recent filings, applies the strategy filters,
computes what it *would* order, and logs it -- no orders are submitted, but
filings are still marked as "seen" so you don't get a wall of duplicate log
lines every run.

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
| `MIN_TRADE_AMOUNT` | Ignore insider trades below this exact dollar value (shares x price -- Form 4 reports both precisely) |
| `MIRROR_TRANSACTION_TYPES` | Which trade types to act on: `Purchase` and/or `Sale` |
| `FOLLOWED_INSIDERS` | Optional allowlist of specific insiders to mirror (by Form 4 name); blank = everyone |
| `LOOKBACK_DAYS` | How many days of SEC's daily filing index to scan each run |
| `MAX_INSIDER_FILINGS_PER_RUN` | Safety cap on filings fetched+parsed per run |

Sell filings only ever close a position this bot already opened for you --
it will never short a stock or sell something you hold for unrelated reasons.
Two different insiders trading the same ticker in opposite directions within
one run (one buying, one selling) resolve correctly regardless of which
filing happened to be scanned first: buys execute before sells each run, and
the sell's "do I actually hold this?" check happens live, right before it
would execute -- not against a stale snapshot from before that run's own
buys went through.

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

When halted, the bot still evaluates and logs every filing (so you can see
what it *would* have done), it just skips submitting orders -- and doesn't
mark those filings as "seen", so they're retried automatically once the halt
clears.

## Audit log

Every filing the bot evaluates -- mirrored or not -- is logged with its
outcome and reason to `data/decisions_log.jsonl`, which the GitHub Actions
workflow commits back to the repo after each run (using GitHub's own
built-in token, no extra secrets needed). The dashboard reads this file
straight from GitHub to show a live audit trail alongside the account data,
including a one-click **Reverse** button on any buy/sell row (sells back if
it bought, buys back if it sold).

## Performance metrics and charts

The dashboard shows Sharpe ratio, max drawdown, and CAGR, computed directly
from Alpaca's own portfolio history endpoint (`get_portfolio_history`) -- no
separate tracking database needed. "Open position win rate" is a simplified
proxy (% of currently open positions with positive unrealized P/L), not a
rigorous realized-P/L trade ledger.

The same portfolio history also drives an **account equity chart** (via
Chart.js, loaded from a CDN) so you can see the trend over time, not just
today's number. Each open position also gets a small **sparkline** showing
its price over the last 90 days (via Alpaca's free historical bars),
colored green or red to match whether that position is currently up or
down.

## News alerts and manual sell

The dashboard fetches recent news (via Alpaca's free News API, same account
keys) for whatever tickers you currently hold, and shows it as a prominent
banner at the top of the page -- so you don't have to wait for an insider's
own sale filing to find out a stock you're mirroring already had bad news.
Each headline has a **Sell** button that closes that position immediately
at market. This is a manual trigger only -- news never auto-sells anything
on its own; you read the headline and decide.

**This is a real trading action reachable from a web page, so the whole
dashboard requires a login before it works.** Set `DASHBOARD_USERNAME` and
`DASHBOARD_PASSWORD` (as Render secret env vars, or in `.env` locally) --
without both set, the dashboard logs a loud warning and runs with no login
at all, which is only acceptable for local testing on your own machine.

## Scheduling

SEC's daily filing index for a given day is only finalized that evening, so
the included workflow runs the morning after each weekday rather than right
after market close -- see `.github/workflows/run-bot.yml`. Two options:

- **Cron on a machine you control** (recommended -- simplest state handling):
  `0 6 * * 2-6 cd /path/to/BriggsTrading && python -m src.main`
- **GitHub Actions** (`.github/workflows/run-bot.yml`, included, disabled
  until you add repo secrets): works, but GitHub's runners are ephemeral so
  the workflow stores `seen_trades.db` as a build artifact and restores it
  each run. That's a bit more fragile than a persistent machine -- if you
  have any server or Raspberry Pi lying around, cron there is more robust.
  Add `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` as repo secrets, and
  `ALPACA_PAPER` / `DRY_RUN` / `CONFIRM_LIVE_TRADING` / `TRADING_HALTED` /
  `MAX_DRAWDOWN_PCT` / `MAX_POSITION_CONCENTRATION_PCT` /
  `MAX_PORTFOLIO_EXPOSURE_PCT` / `MIRROR_TRANSACTION_TYPES` /
  `MIN_TRADE_AMOUNT` / `LOOKBACK_DAYS` / `MAX_INSIDER_FILINGS_PER_RUN` /
  `FOLLOWED_INSIDERS` / `SEC_EDGAR_USER_AGENT` (all optional, sensible
  defaults baked in) as repo variables, to use it. The workflow also needs
  `contents: write` permission (already set in the file) so it can commit
  the audit log back to the repo after each run.

Given how long a full scan can take (see "How it finds trades" above), check
your Actions usage if this repo is private -- a public repo gets unlimited
free minutes.

## Dashboard

`dashboard/` is a small Flask app that shows account equity, open positions,
and recent orders this bot has placed -- polling every 30 seconds. It reads
live from Alpaca; it doesn't need the bot's cron job to be running to show
current state.

Run it locally:
```
python -m flask --app dashboard.app run
```
then open http://127.0.0.1:5000.

Deploy it on Render using the included `render.yaml`: create a new Blueprint
from this repo in the Render dashboard, and set `ALPACA_API_KEY`,
`ALPACA_SECRET_KEY` as secret env vars there (they're marked `sync: false` so
Render prompts for them rather than storing them in the repo).

## Real Holdings tracker (optional)

The dashboard has a "Real Holdings (manual) vs. Paper Bot" section for
tracking your own actual investments -- accounts the bot has no API access to
(Schwab, a 401k, pre-IPO shares, whatever) -- side by side with the paper
bot's return %, so you can see whether the bot's trades are actually beating
what you're already doing.

Entries are added by hand from the dashboard (ticker or fund name, shares,
cost per share, and which account it's in). There's no brokerage integration
-- you update shares/cost as your statements change, e.g. after each 401k
contribution.

Pricing: if the ticker is one Alpaca has a live quote for, that's used
automatically. Otherwise (a mutual fund like a Vanguard or JPMorgan fund, or
a private company's stock) enter a "manual price" yourself and update it
periodically from your statement -- the table marks each row `live` or
`manual` so it's clear which is which.

This is a directional comparison only: your real accounts may include
contributions or withdrawals that a simple return % doesn't separate from
actual investment gains, so treat it as a rough read, not an exact one.

Entries persist as `data/real_holdings.json`, committed to this repo via the
GitHub Contents API (same mechanism as the audit log, just read/write instead
of append-only) -- there's no database. Set `GITHUB_PAT` (a token with
`repo` scope, or a fine-grained token with Contents read/write on this repo)
for the dashboard to use it; leave it unset and the section just stays empty
with add/edit/delete disabled.

## Known limitations

- SEC EDGAR's daily index file format has been stable for years, but if
  `src/sec_edgar_client.py` starts erroring on a 403 that isn't "day doesn't
  exist," check https://www.sec.gov/os/webmaster-faq#developers for current
  fair-access requirements (SEC blocks requests with no identifying contact
  info outright).
- Orders are simple market orders sized as a fraction of account equity --
  there's no stop-loss, take-profit, or portfolio rebalancing logic.
- Only mirrors single stocks Alpaca can trade; the issuer on a Form 4 is
  sometimes a foreign private issuer, a fund, or otherwise untradeable there,
  in which case it's just skipped.
- A sale is a much noisier signal than a purchase -- insiders sell for
  routine reasons (diversification, taxes, pre-scheduled 10b5-1 plans) that
  have nothing to do with their view of the stock. Consider setting
  `MIRROR_TRANSACTION_TYPES=Purchase` only if that bothers you.
