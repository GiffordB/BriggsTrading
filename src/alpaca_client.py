import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

logger = logging.getLogger(__name__)


class AlpacaClient:
    def __init__(self, api_key: str, secret_key: str, paper: bool):
        self._client = TradingClient(api_key, secret_key, paper=paper)
        self._news_client = NewsClient(api_key, secret_key)
        self._data_client = StockHistoricalDataClient(api_key, secret_key)

    def get_equity(self) -> float:
        account = self._client.get_account()
        return float(account.equity)

    def get_buying_power(self) -> float:
        account = self._client.get_account()
        return float(account.buying_power)

    def get_account_summary(self) -> dict:
        account = self._client.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        return {
            "equity": equity,
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "day_pl": equity - last_equity,
            "day_pl_pct": (equity - last_equity) / last_equity if last_equity else 0.0,
        }

    def list_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> list[dict]:
        request = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        history = self._client.get_portfolio_history(request)
        return [
            {"timestamp": ts, "equity": equity}
            for ts, equity in zip(history.timestamp or [], history.equity or [])
            if equity is not None
        ]

    def list_recent_orders(self, limit: int = 25) -> list[dict]:
        request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
        orders = self._client.get_orders(request)
        return [
            {
                "symbol": o.symbol,
                "side": o.side.value,
                "notional": float(o.notional) if o.notional else None,
                "qty": float(o.qty) if o.qty else None,
                "status": o.status.value,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            }
            for o in orders
        ]

    def has_open_position(self, ticker: str) -> bool:
        try:
            self._client.get_open_position(ticker)
            return True
        except Exception:
            return False

    def is_tradable(self, ticker: str) -> bool:
        try:
            asset = self._client.get_asset(ticker)
            return bool(asset.tradable)
        except Exception:
            return False

    def submit_market_order(self, ticker: str, notional: float, side: OrderSide):
        order = MarketOrderRequest(
            symbol=ticker,
            notional=round(notional, 2),
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        return self._client.submit_order(order)

    def close_position(self, ticker: str):
        return self._client.close_position(ticker)

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        stop_loss_price: float,
        take_profit_price: float | None,
    ):
        """A market entry with Alpaca-enforced stop-loss (and optional
        take-profit) attached -- the stop/target half of a strategy's exit
        rule is enforced by the exchange itself, not by a bot that has to be
        running at the right moment. The max_holding_days time-exit still
        has to be handled by a later run finding the position still open
        (see state.PositionTracker) -- Alpaca brackets don't support that."""
        order_side = OrderSide.BUY if side == "long" else OrderSide.SELL
        # BRACKET needs both legs; OTO (one-triggers-other) covers a
        # stop-only exit for a strategy with no target_pct.
        order_class = OrderClass.BRACKET if take_profit_price else OrderClass.OTO
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            order_class=order_class,
            take_profit=TakeProfitRequest(limit_price=take_profit_price)
            if take_profit_price
            else None,
            stop_loss=StopLossRequest(stop_price=stop_loss_price),
        )
        return self._client.submit_order(order)

    def get_recent_news(self, symbols: list[str], lookback_days: int, limit: int = 50) -> list[dict]:
        """Recent news headlines for the given tickers, via Alpaca's News API --
        no extra signup needed, works with the same account keys."""
        if not symbols:
            return []
        request = NewsRequest(
            symbols=symbols,
            start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
            limit=limit,
        )
        news_set = self._news_client.get_news(request)
        articles = news_set.data.get("news", [])
        return [
            {
                "headline": a.headline,
                "source": a.source,
                "url": a.url,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "symbols": a.symbols,
            }
            for a in sorted(articles, key=lambda a: a.created_at, reverse=True)
        ]

    def get_price_history(self, symbols: list[str], lookback_days: int) -> dict[str, list[dict]]:
        """Daily close price series per symbol, for position sparklines. Uses the
        free IEX feed, which works without a paid SIP subscription."""
        if not symbols:
            return {}
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
            feed=DataFeed.IEX,
        )
        bar_set = self._data_client.get_stock_bars(request)
        return {
            symbol: [
                {"date": bar.timestamp.date().isoformat(), "close": float(bar.close)}
                for bar in bars
            ]
            for symbol, bars in bar_set.data.items()
        }

    def get_daily_bars(
        self, symbols: list[str], years: float, chunk_size: int = 50
    ) -> dict[str, list[dict]]:
        """Full daily OHLCV history for backtesting, chunked across requests to
        keep any single request's symbol list a manageable size. Free IEX feed
        -- years of real daily bars, no paid subscription. A chunk that fails
        (rate limit, transient error) is skipped with a warning rather than
        failing the whole scan; the caller sees it's simply missing from the
        result and the next run tries again."""
        if not symbols:
            return {}
        start = datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))
        results: dict[str, list[dict]] = {}
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i : i + chunk_size]
            request = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame.Day,
                start=start,
                feed=DataFeed.IEX,
            )
            try:
                bar_set = self._data_client.get_stock_bars(request)
            except Exception:
                logger.warning("Failed to fetch daily bars for chunk %s", chunk, exc_info=True)
                continue
            for symbol, bars in bar_set.data.items():
                results[symbol] = [
                    {
                        "date": bar.timestamp.date().isoformat(),
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume),
                    }
                    for bar in bars
                ]
        return results
