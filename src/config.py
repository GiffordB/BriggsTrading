import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Config:
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_paper: bool = _bool("ALPACA_PAPER", True)
    confirm_live_trading: str = os.getenv("CONFIRM_LIVE_TRADING", "")

    # --- Risk (independent of any strategy's own signal -- see risk_guard.py) ---
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))
    daily_loss_limit_pct: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.03"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", "2.0"))
    allow_short: bool = _bool("ALLOW_SHORT", True)
    max_position_concentration_pct: float = float(
        os.getenv("MAX_POSITION_CONCENTRATION_PCT", "0.20")
    )
    max_portfolio_exposure_pct: float = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.75"))
    # Cap position size at this fraction of full Kelly, on top of the flat
    # risk_per_trade_pct cap -- whichever is smaller wins. Guards against the
    # backtest's own win-rate/avg-win estimate being too optimistic.
    kelly_fraction_cap: float = float(os.getenv("KELLY_FRACTION_CAP", "0.25"))

    # --- Research pipeline (src/research_pipeline.py) ---
    # How many years of daily bars to backtest over.
    backtest_years: int = int(os.getenv("BACKTEST_YEARS", "5"))
    # Only strategies scoring at or above this (out of 10, see src/rank.py)
    # make the morning shortlist.
    min_shortlist_score: float = float(os.getenv("MIN_SHORTLIST_SCORE", "8.0"))
    # Max new entries the execution step will act on in one morning, even if
    # more survive -- see playbook.md.
    max_setups_per_morning: int = int(os.getenv("MAX_SETUPS_PER_MORNING", "2"))
    # Minimum number of out-of-sample trades a backtest must produce to even
    # be considered (the "sample size" gate in src/skeptic.py).
    min_backtest_trades: int = int(os.getenv("MIN_BACKTEST_TRADES", "30"))

    dry_run: bool = _bool("DRY_RUN", True)
    trading_halted: bool = _bool("TRADING_HALTED", False)

    news_lookback_days: int = int(os.getenv("NEWS_LOOKBACK_DAYS", "5"))

    # Dashboard-only: gates every route with HTTP Basic Auth. Required once the
    # dashboard can place trades (the manual sell button) -- see dashboard/app.py.
    dashboard_username: str = os.getenv("DASHBOARD_USERNAME", "")
    dashboard_password: str = os.getenv("DASHBOARD_PASSWORD", "")

    # Dashboard-only: lets the "Real Holdings" tracker persist manual entries by
    # committing to data/real_holdings.json via the GitHub Contents API -- the
    # dashboard has no database of its own. Needs `repo` scope (classic PAT) or
    # Contents read/write (fine-grained PAT) on GiffordB/BriggsTrading.
    github_pat: str = os.getenv("GITHUB_PAT", "")
    github_repo: str = os.getenv("GITHUB_REPO", "GiffordB/BriggsTrading")

    def validate(self) -> None:
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        if not self.alpaca_paper and self.confirm_live_trading != "I-UNDERSTAND-THIS-IS-REAL-MONEY":
            raise ValueError(
                "ALPACA_PAPER=false but CONFIRM_LIVE_TRADING is not set to "
                "'I-UNDERSTAND-THIS-IS-REAL-MONEY'. Refusing to trade with real money "
                "without explicit, deliberate confirmation."
            )
