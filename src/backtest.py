"""Offline backtest of the congress-mirroring strategy.

Run locally with: python -m src.backtest

Simulates buying/selling at each disclosure's *filed* date (never earlier --
that's the earliest point the strategy could actually have known about the
trade), using Alpaca's free historical daily bars for prices. This is a real
but simplified simulation: equity is only marked-to-market at trade-event
dates (not daily), there's no slippage/commission modeling, and it assumes
orders always fill at the day's close. Treat the output as a rough sanity
check of the strategy, not an investment projection.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from .config import Config
from .metrics import compute_metrics
from .quiver_client import Disclosure, QuiverClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SELL_TRANSACTION_TYPES = {"sale (full)", "sale (partial)", "sale"}


def _passes_basic_filters(disclosure: Disclosure, config: Config) -> bool:
    if disclosure.transaction_type not in config.mirror_transaction_types:
        return False
    if disclosure.amount_low < config.min_trade_amount:
        return False
    if config.followed_members and disclosure.representative not in config.followed_members:
        return False
    return True


def _fetch_price_series(
    data_client: StockHistoricalDataClient, tickers: list[str], start: date, end: date
) -> dict[str, list[tuple[date, float]]]:
    if not tickers:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc),
        feed=DataFeed.IEX,  # SIP feed needs a paid subscription; IEX works on free accounts
    )
    bar_set = data_client.get_stock_bars(request)
    series: dict[str, list[tuple[date, float]]] = {}
    for ticker in tickers:
        bars = bar_set.data.get(ticker, [])
        series[ticker] = [(bar.timestamp.date(), float(bar.close)) for bar in bars]
    return series


def _price_on_or_before(series: list[tuple[date, float]], target: date) -> float | None:
    candidates = [price for d, price in series if d <= target]
    return candidates[-1] if candidates else None


def run_backtest() -> None:
    config = Config()
    if not config.quiver_api_token or not config.alpaca_api_key or not config.alpaca_secret_key:
        raise ValueError("QUIVER_API_TOKEN / ALPACA_API_KEY / ALPACA_SECRET_KEY must be set")

    days = int(os.getenv("BACKTEST_DAYS", "180"))
    starting_cash = float(os.getenv("BACKTEST_STARTING_CASH", "100000"))
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    logger.info("Backtesting %s to %s with $%.2f starting cash", start_date, end_date, starting_cash)

    quiver = QuiverClient(config.quiver_api_token)
    disclosures = quiver.fetch_historical_congress_trades(start_date, end_date)
    qualifying = [d for d in disclosures if _passes_basic_filters(d, config)]
    logger.info(
        "%d disclosures in range, %d pass the configured filters", len(disclosures), len(qualifying)
    )

    if not qualifying:
        logger.warning("No disclosures passed the filters in this window -- nothing to simulate")
        return

    tickers = sorted({d.ticker for d in qualifying})
    data_client = StockHistoricalDataClient(config.alpaca_api_key, config.alpaca_secret_key)
    price_series = _fetch_price_series(data_client, tickers, start_date, end_date)

    cash = starting_cash
    positions: dict[str, dict] = {}  # ticker -> {"qty": float, "entry_price": float}
    equity_curve: list[dict] = []
    closed_trade_pnls: list[float] = []

    def mark_to_market(as_of: date) -> float:
        value = cash
        for ticker, pos in positions.items():
            price = _price_on_or_before(price_series.get(ticker, []), as_of) or pos["entry_price"]
            value += pos["qty"] * price
        return value

    for disclosure in qualifying:
        filed = datetime.fromisoformat(disclosure.filed_date.replace("Z", "+00:00")).date()
        price = _price_on_or_before(price_series.get(disclosure.ticker, []), filed)
        if price is None:
            logger.info("No price data for %s around %s, skipping", disclosure.ticker, filed)
            continue

        transaction_type_lower = disclosure.transaction_type.lower()
        if transaction_type_lower in _SELL_TRANSACTION_TYPES:
            pos = positions.get(disclosure.ticker)
            if not pos:
                continue
            proceeds = pos["qty"] * price
            realized_pnl = proceeds - pos["qty"] * pos["entry_price"]
            cash += proceeds
            closed_trade_pnls.append(realized_pnl)
            del positions[disclosure.ticker]
        else:
            current_equity = mark_to_market(filed)
            notional = min(current_equity * config.position_size_pct, config.max_notional_per_trade, cash)
            if notional < 1:
                continue
            qty = notional / price
            cash -= notional
            existing = positions.get(disclosure.ticker)
            if existing:
                total_qty = existing["qty"] + qty
                existing["entry_price"] = (
                    existing["qty"] * existing["entry_price"] + qty * price
                ) / total_qty
                existing["qty"] = total_qty
            else:
                positions[disclosure.ticker] = {"qty": qty, "entry_price": price}

        equity_curve.append({"timestamp": int(datetime.combine(filed, datetime.min.time()).timestamp()), "equity": mark_to_market(filed)})

    final_equity = mark_to_market(end_date)
    equity_curve.append({"timestamp": int(datetime.combine(end_date, datetime.min.time()).timestamp()), "equity": final_equity})

    metrics = compute_metrics(equity_curve, positions=[])
    total_return_pct = (final_equity - starting_cash) / starting_cash * 100
    win_rate_pct = (
        (sum(1 for p in closed_trade_pnls if p > 0) / len(closed_trade_pnls) * 100)
        if closed_trade_pnls
        else None
    )

    print("\n=== Backtest Results ===")
    print(f"Period: {start_date} to {end_date} ({days} days)")
    print(f"Starting cash: ${starting_cash:,.2f}")
    print(f"Ending equity: ${final_equity:,.2f}")
    print(f"Total return: {total_return_pct:.2f}%")
    print(f"CAGR: {metrics.cagr_pct:.2f}%" if metrics.cagr_pct is not None else "CAGR: n/a (not enough data)")
    print(f"Max drawdown: {metrics.max_drawdown_pct:.2f}%")
    print(f"Sharpe ratio: {metrics.sharpe_ratio:.2f}" if metrics.sharpe_ratio is not None else "Sharpe ratio: n/a (not enough data)")
    print(f"Closed round-trip trades: {len(closed_trade_pnls)}")
    print(f"Win rate (closed trades): {win_rate_pct:.1f}%" if win_rate_pct is not None else "Win rate: n/a (no closed trades)")
    print(f"Still-open positions at end: {len(positions)}")


if __name__ == "__main__":
    run_backtest()
