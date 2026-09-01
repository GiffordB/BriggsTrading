import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _list(name: str) -> list[str]:
    val = os.getenv(name, "")
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    quiver_api_token: str = os.getenv("QUIVER_API_TOKEN", "")

    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_paper: bool = _bool("ALPACA_PAPER", True)
    confirm_live_trading: str = os.getenv("CONFIRM_LIVE_TRADING", "")

    position_size_pct: float = float(os.getenv("POSITION_SIZE_PCT", "0.02"))
    max_notional_per_trade: float = float(os.getenv("MAX_NOTIONAL_PER_TRADE", "2000"))
    max_notional_per_run: float = float(os.getenv("MAX_NOTIONAL_PER_RUN", "5000"))
    min_trade_amount: float = float(os.getenv("MIN_TRADE_AMOUNT", "15000"))
    mirror_transaction_types: list[str] = field(
        default_factory=lambda: _list("MIRROR_TRANSACTION_TYPES") or ["Purchase"]
    )
    followed_members: list[str] = field(default_factory=lambda: _list("FOLLOWED_MEMBERS"))
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", "7"))
    dry_run: bool = _bool("DRY_RUN", True)

    trading_halted: bool = _bool("TRADING_HALTED", False)
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
    max_position_concentration_pct: float = float(
        os.getenv("MAX_POSITION_CONCENTRATION_PCT", "0.20")
    )
    max_portfolio_exposure_pct: float = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.75"))

    require_confirming_signal: bool = _bool("REQUIRE_CONFIRMING_SIGNAL", False)
    confirming_signal_lookback_days: int = int(os.getenv("CONFIRMING_SIGNAL_LOOKBACK_DAYS", "90"))

    # Free alternative to Quiver's paid Insider Trading tier -- see
    # src/sec_edgar_client.py. Only used when REQUIRE_CONFIRMING_SIGNAL is on.
    sec_edgar_user_agent: str = os.getenv(
        "SEC_EDGAR_USER_AGENT", "BriggsTrading github.com/GiffordB/BriggsTrading"
    )

    news_lookback_days: int = int(os.getenv("NEWS_LOOKBACK_DAYS", "5"))

    # Dashboard-only: gates every route with HTTP Basic Auth. Required once the
    # dashboard can place trades (the manual sell button) -- see dashboard/app.py.
    dashboard_username: str = os.getenv("DASHBOARD_USERNAME", "")
    dashboard_password: str = os.getenv("DASHBOARD_PASSWORD", "")

    def validate(self) -> None:
        if not self.quiver_api_token:
            raise ValueError("QUIVER_API_TOKEN is not set")
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        if not self.alpaca_paper and self.confirm_live_trading != "I-UNDERSTAND-THIS-IS-REAL-MONEY":
            raise ValueError(
                "ALPACA_PAPER=false but CONFIRM_LIVE_TRADING is not set to "
                "'I-UNDERSTAND-THIS-IS-REAL-MONEY'. Refusing to trade with real money "
                "without explicit, deliberate confirmation."
            )
