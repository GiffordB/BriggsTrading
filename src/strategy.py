import logging
from dataclasses import dataclass

from alpaca.trading.enums import OrderSide

from .alpaca_client import AlpacaClient
from .config import Config
from .quiver_client import Disclosure

logger = logging.getLogger(__name__)

_SELL_TRANSACTION_TYPES = {"sale (full)", "sale (partial)", "sale"}


@dataclass(frozen=True)
class Decision:
    disclosure: Disclosure
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


def _filter_reason(disclosure: Disclosure, config: Config) -> str | None:
    """Returns a skip reason if the disclosure fails the basic filters, else None."""
    if disclosure.transaction_type not in config.mirror_transaction_types:
        return (
            f"transaction type '{disclosure.transaction_type}' not in "
            f"MIRROR_TRANSACTION_TYPES {config.mirror_transaction_types}"
        )
    if disclosure.amount_low < config.min_trade_amount:
        return (
            f"amount range '{disclosure.raw_range}' below MIN_TRADE_AMOUNT "
            f"(${config.min_trade_amount:,.0f})"
        )
    if config.followed_members and disclosure.representative not in config.followed_members:
        return f"'{disclosure.representative}' not in FOLLOWED_MEMBERS"
    return None


def evaluate_disclosures(
    disclosures: list[Disclosure],
    config: Config,
    broker: AlpacaClient,
    confirming_tickers: set[str] | None = None,
) -> list[Decision]:
    """Evaluates every disclosure and returns a Decision for each one -- including
    skips, with a human-readable reason -- so the full set can be logged for audit,
    not just the ones that end up as orders.

    `confirming_tickers`, when REQUIRE_CONFIRMING_SIGNAL is on, is the set of
    tickers with recent lobbying or government contract activity (fetched once
    per run by main.py). Passing None means either the feature is off or that
    data was unavailable this run -- either way, the filter is not applied,
    rather than blocking every purchase.

    Only makes read-only broker calls (tradability, existing positions, equity), so
    this is always safe to call, including in DRY_RUN mode.
    """
    equity = broker.get_equity()
    buying_power = broker.get_buying_power()
    per_trade_notional = min(equity * config.position_size_pct, config.max_notional_per_trade)
    remaining_run_budget = min(config.max_notional_per_run, buying_power)

    decisions: list[Decision] = []

    for disclosure in disclosures:
        filter_reason = _filter_reason(disclosure, config)
        if filter_reason:
            decisions.append(Decision(disclosure, "skip", filter_reason))
            continue

        if not broker.is_tradable(disclosure.ticker):
            decisions.append(Decision(disclosure, "skip", "not tradable on Alpaca"))
            continue

        transaction_type_lower = disclosure.transaction_type.lower()
        if transaction_type_lower in _SELL_TRANSACTION_TYPES:
            if not broker.has_open_position(disclosure.ticker):
                decisions.append(
                    Decision(disclosure, "skip", "no open position to sell")
                )
                continue
            decisions.append(Decision(disclosure, "sell", "mirroring disclosed sale"))
            continue

        if (
            config.require_confirming_signal
            and confirming_tickers is not None
            and disclosure.ticker not in confirming_tickers
        ):
            decisions.append(
                Decision(
                    disclosure,
                    "skip",
                    f"no recent lobbying/gov-contract activity for {disclosure.ticker} "
                    f"in the last {config.confirming_signal_lookback_days} days",
                )
            )
            continue

        notional = min(per_trade_notional, remaining_run_budget)
        if notional < 1:
            decisions.append(
                Decision(disclosure, "skip", "MAX_NOTIONAL_PER_RUN budget exhausted")
            )
            continue

        reason = "mirroring disclosed purchase"
        if config.require_confirming_signal and confirming_tickers is not None:
            reason += " (confirmed by recent lobbying/gov-contract activity)"
        decisions.append(Decision(disclosure, "buy", reason, notional=notional))
        remaining_run_budget -= notional

    return decisions
