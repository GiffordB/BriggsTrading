from datetime import datetime, timedelta, timezone

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest, MarketOrderRequest


class AlpacaClient:
    def __init__(self, api_key: str, secret_key: str, paper: bool):
        self._client = TradingClient(api_key, secret_key, paper=paper)
        self._news_client = NewsClient(api_key, secret_key)

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
