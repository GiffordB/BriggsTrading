from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


class AlpacaClient:
    def __init__(self, api_key: str, secret_key: str, paper: bool):
        self._client = TradingClient(api_key, secret_key, paper=paper)

    def get_equity(self) -> float:
        account = self._client.get_account()
        return float(account.equity)

    def get_buying_power(self) -> float:
        account = self._client.get_account()
        return float(account.buying_power)

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
