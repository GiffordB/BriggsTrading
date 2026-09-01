from dataclasses import dataclass, field

from .alpaca_client import AlpacaClient
from .config import Config


@dataclass(frozen=True)
class RiskStatus:
    halted: bool
    reasons: list[str] = field(default_factory=list)
    current_drawdown_pct: float = 0.0
    total_exposure_pct: float = 0.0


def assess_risk(broker: AlpacaClient, config: Config) -> RiskStatus:
    """Independent, pre-trade risk check.

    Deliberately separate from strategy.py's per-trade sizing: a bug in the
    strategy's own filters can't bypass this, since it never calls into it.
    """
    reasons = []

    if config.trading_halted:
        reasons.append("Manual kill switch (TRADING_HALTED) is set")

    # An unknown risk state is treated as a halt, not a green light -- if we can't
    # verify the account is within limits, we don't trade, rather than silently
    # skipping the check or crashing the whole bot run.
    try:
        account = broker.get_account_summary()
        equity = account["equity"]

        history = broker.get_portfolio_history(period="3M", timeframe="1D")
        equities = [h["equity"] for h in history] + [equity]
        peak = max(equities) if equities else equity
        drawdown_pct = (peak - equity) / peak if peak else 0.0
        if drawdown_pct >= config.max_drawdown_pct:
            reasons.append(
                f"Portfolio drawdown {drawdown_pct:.1%} exceeds MAX_DRAWDOWN_PCT "
                f"({config.max_drawdown_pct:.1%})"
            )

        positions = broker.list_positions()
        total_exposure_pct = (
            sum(abs(p["market_value"]) for p in positions) / equity if equity else 0.0
        )
        if total_exposure_pct >= config.max_portfolio_exposure_pct:
            reasons.append(
                f"Total exposure {total_exposure_pct:.1%} exceeds MAX_PORTFOLIO_EXPOSURE_PCT "
                f"({config.max_portfolio_exposure_pct:.1%})"
            )
    except Exception as exc:
        reasons.append(f"Could not verify account risk state, failing safe: {exc}")
        return RiskStatus(halted=True, reasons=reasons)

    return RiskStatus(
        halted=bool(reasons),
        reasons=reasons,
        current_drawdown_pct=drawdown_pct,
        total_exposure_pct=total_exposure_pct,
    )


def breaches_concentration_limit(
    ticker: str, additional_notional: float, broker: AlpacaClient, config: Config
) -> bool:
    """True if buying `additional_notional` more of `ticker` would push that single
    position above MAX_POSITION_CONCENTRATION_PCT of account equity."""
    account = broker.get_account_summary()
    equity = account["equity"]
    if equity <= 0:
        return False
    existing = next(
        (p["market_value"] for p in broker.list_positions() if p["symbol"] == ticker), 0.0
    )
    projected_pct = (existing + additional_notional) / equity
    return projected_pct > config.max_position_concentration_pct
