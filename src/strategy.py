import logging
from dataclasses import dataclass

from alpaca.trading.enums import OrderSide

from .alpaca_client import AlpacaClient
from .config import Config
from .quiver_client import Disclosure

logger = logging.getLogger(__name__)

_SELL_TRANSACTION_TYPES = {"sale (full)", "sale (partial)", "sale"}


@dataclass(frozen=True)
class PlannedOrder:
    disclosure: Disclosure
    side: OrderSide
    notional: float


def _passes_filters(disclosure: Disclosure, config: Config) -> bool:
    if disclosure.transaction_type not in config.mirror_transaction_types:
        return False
    if disclosure.amount_low < config.min_trade_amount:
        return False
    if config.followed_members and disclosure.representative not in config.followed_members:
        return False
    return True


def plan_orders(
    disclosures: list[Disclosure], config: Config, broker: AlpacaClient
) -> list[PlannedOrder]:
    """Turn qualifying disclosures into concrete, sized orders.

    Only decides *what* to trade -- it never talks to the broker beyond read-only
    checks (tradability, existing positions, equity), so it's safe to call in
    DRY_RUN mode.
    """
    equity = broker.get_equity()
    buying_power = broker.get_buying_power()
    per_trade_notional = min(equity * config.position_size_pct, config.max_notional_per_trade)

    planned: list[PlannedOrder] = []
    remaining_run_budget = min(config.max_notional_per_run, buying_power)

    for disclosure in disclosures:
        if not _passes_filters(disclosure, config):
            continue

        if not broker.is_tradable(disclosure.ticker):
            logger.info("Skipping %s: not tradable on Alpaca", disclosure.ticker)
            continue

        transaction_type_lower = disclosure.transaction_type.lower()
        if transaction_type_lower in _SELL_TRANSACTION_TYPES:
            if not broker.has_open_position(disclosure.ticker):
                logger.info(
                    "Skipping sell mirror for %s: no open position to sell", disclosure.ticker
                )
                continue
            planned.append(PlannedOrder(disclosure=disclosure, side=OrderSide.SELL, notional=0))
            continue

        notional = min(per_trade_notional, remaining_run_budget)
        if notional < 1:
            logger.info("Run notional budget exhausted, skipping remaining disclosures")
            break

        planned.append(PlannedOrder(disclosure=disclosure, side=OrderSide.BUY, notional=notional))
        remaining_run_budget -= notional

    return planned
