import logging
from dataclasses import dataclass

from alpaca.trading.enums import OrderSide

from .alpaca_client import AlpacaClient
from .config import Config
from .sec_edgar_client import InsiderFiling

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    filing: InsiderFiling
    action: str  # "buy", "sell", or "skip"
    reason: str
    notional: float = 0.0

    @property
    def side(self) -> OrderSide | None:
        if self.action == "buy":
            return OrderSide.BUY
        if self.action == "sell":
            return OrderSide.SELL
        return None


def _filter_reason(filing: InsiderFiling, config: Config) -> str | None:
    """Returns a skip reason if the filing fails the basic filters, else None."""
    configured = {t.strip().lower() for t in config.mirror_transaction_types}
    if filing.transaction_type.lower() not in configured:
        return (
            f"transaction type '{filing.transaction_type}' not in "
            f"MIRROR_TRANSACTION_TYPES {config.mirror_transaction_types}"
        )
    if filing.notional < config.min_trade_amount:
        return (
            f"trade value ${filing.notional:,.0f} below MIN_TRADE_AMOUNT "
            f"(${config.min_trade_amount:,.0f})"
        )
    if config.followed_insiders and filing.insider_name not in config.followed_insiders:
        return f"'{filing.insider_name}' not in FOLLOWED_INSIDERS"
    return None


def evaluate_insider_filings(
    filings: list[InsiderFiling], config: Config, broker: AlpacaClient
) -> list[Decision]:
    """Evaluates every insider filing and returns a Decision for each one --
    including skips, with a human-readable reason -- so the full set can be
    logged for audit, not just the ones that end up as orders.

    Only makes read-only broker calls (tradability, equity), so this is
    always safe to call, including in DRY_RUN mode. Does not check existing
    positions for sell decisions -- see the note in the sell branch below for
    why that check is deferred to main.py's execution loop instead.
    """
    equity = broker.get_equity()
    buying_power = broker.get_buying_power()
    per_trade_notional = min(equity * config.position_size_pct, config.max_notional_per_trade)
    remaining_run_budget = min(config.max_notional_per_run, buying_power)

    decisions: list[Decision] = []

    for filing in filings:
        filter_reason = _filter_reason(filing, config)
        if filter_reason:
            decisions.append(Decision(filing, "skip", filter_reason))
            continue

        if not broker.is_tradable(filing.ticker):
            decisions.append(Decision(filing, "skip", "not tradable on Alpaca"))
            continue

        if filing.transaction_type == "Sale":
            # Not checking has_open_position here: within a single run, an
            # earlier filing's buy for this same ticker (from a different
            # insider) may not have executed yet at evaluation time, which
            # would make this check wrongly and *permanently* skip a
            # legitimate sell (a non-retryable skip marks the filing seen
            # forever). main.py re-checks live position state immediately
            # before execution instead, the same way it already does for the
            # buy-side concentration limit.
            decisions.append(Decision(filing, "sell", "mirroring insider sale"))
            continue

        notional = min(per_trade_notional, remaining_run_budget)
        if notional < 1:
            decisions.append(
                Decision(filing, "skip", "MAX_NOTIONAL_PER_RUN budget exhausted")
            )
            continue

        decisions.append(Decision(filing, "buy", "mirroring insider purchase", notional=notional))
        remaining_run_budget -= notional

    return decisions
