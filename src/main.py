import logging
from datetime import datetime, timezone

from alpaca.trading.enums import OrderSide

from . import decisions_log
from .alpaca_client import AlpacaClient
from .config import Config
from .quiver_client import QuiverClient
from .risk_guard import assess_risk, breaches_concentration_limit
from .sec_edgar_client import SECEdgarClient
from .state import SeenTradesStore
from .strategy import evaluate_disclosures

# Form 4 transaction code -> the action it implies for the insider-override
# check. Only the two genuinely discretionary, directional codes count.
_INSIDER_ACTION_BY_CODE = {"P": "buy", "S": "sell"}


def _default_trade_notional(broker: AlpacaClient, config: Config) -> float:
    """Per-trade notional for a buy created purely by an insider override --
    i.e. one that didn't go through evaluate_disclosures's own run-budget
    sizing because the original disclosure decision wasn't a buy."""
    return min(
        broker.get_equity() * config.position_size_pct,
        config.max_notional_per_trade,
        broker.get_buying_power(),
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)

# Skip reasons that are about run-specific or portfolio-state-specific capacity
# rather than something inherent to the disclosure -- these should be retried
# on a later run rather than permanently marked "seen".
_RETRYABLE_SKIP_REASONS = {"MAX_NOTIONAL_PER_RUN budget exhausted"}


def run() -> None:
    config = Config()
    config.validate()

    if config.dry_run:
        logger.warning("Running in DRY_RUN mode: no orders will be submitted")
    if config.alpaca_paper:
        logger.info("Trading against Alpaca PAPER account")
    else:
        logger.warning("Trading against Alpaca LIVE account with REAL MONEY")

    quiver = QuiverClient(config.quiver_api_token)
    broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
    sec_edgar = SECEdgarClient(config.sec_edgar_user_agent)
    store = SeenTradesStore()

    try:
        risk_status = assess_risk(broker, config)
        logger.info(
            "Risk check: halted=%s drawdown=%.1f%% exposure=%.1f%%",
            risk_status.halted,
            risk_status.current_drawdown_pct * 100,
            risk_status.total_exposure_pct * 100,
        )
        for reason in risk_status.reasons:
            logger.warning("Risk guard: %s", reason)

        disclosures = quiver.fetch_recent_congress_trades(config.lookback_days)
        logger.info("Fetched %d disclosures from the last %d days", len(disclosures), config.lookback_days)

        new_disclosures = [d for d in disclosures if not store.has_seen(d.dedupe_key)]
        logger.info("%d disclosures are new (not previously acted on)", len(new_disclosures))

        confirming_tickers = None
        if config.require_confirming_signal:
            lobbying = quiver.fetch_recent_lobbying_tickers(config.confirming_signal_lookback_days)
            contracts = quiver.fetch_recent_gov_contract_tickers(config.confirming_signal_lookback_days)
            if lobbying is None and contracts is None:
                logger.warning(
                    "Confirming signal data unavailable this run; skipping that filter"
                )
            else:
                confirming_tickers = (lobbying or set()) | (contracts or set())
                logger.info(
                    "Confirming signal: %d tickers with recent lobbying/gov-contract activity",
                    len(confirming_tickers),
                )

        decisions = evaluate_disclosures(
            new_disclosures, config, broker, confirming_tickers, sec_edgar
        )

        # Execute buys before sells: if two different members' disclosures for the
        # same ticker land in one run (one buying, one selling), this ensures a
        # position created by this run's own buy is visible to this run's sell
        # check below, rather than the sell always finding nothing to sell just
        # because it happened to be evaluated first.
        decisions = sorted(decisions, key=lambda dec: dec.action != "buy")

        for decision in decisions:
            d = decision.disclosure
            final_action = decision.action
            final_reason = decision.reason
            final_notional = decision.notional
            skip_retryable = False
            insider_override = False
            insider_detail = ""

            if risk_status.halted and decision.action != "skip":
                # Unconditional stop -- takes priority over everything below,
                # including an insider override.
                final_action = "skip"
                final_reason = "blocked by risk guard: " + "; ".join(risk_status.reasons)
                skip_retryable = True
            else:
                if config.insider_override_enabled:
                    insider_txn = sec_edgar.most_recent_directional_transaction(
                        d.ticker, config.insider_override_lookback_days
                    )
                    insider_action = _INSIDER_ACTION_BY_CODE.get(
                        insider_txn.transaction_code if insider_txn else ""
                    )
                    if insider_action and insider_action != final_action:
                        insider_override = True
                        role = (
                            "officer" if insider_txn.is_officer
                            else "director" if insider_txn.is_director
                            else "insider"
                        )
                        insider_detail = (
                            f"{insider_txn.insider_name} ({role}) filed a Form 4 "
                            f"{'purchase' if insider_action == 'buy' else 'sale'} on "
                            f"{insider_txn.transaction_date} -- overrides the congressional "
                            f"{final_action if final_action in ('buy', 'sell') else 'signal'} "
                            f"for {d.ticker} (fresher signal: 2-day insider filing window vs. "
                            f"Congress's 30-45 day one)"
                        )
                        logger.warning("INSIDER OVERRIDE %s: %s", d.ticker, insider_detail)
                        final_action = insider_action
                        final_reason = insider_detail
                        if final_action == "buy" and not final_notional:
                            final_notional = _default_trade_notional(broker, config)

                if decision.action == "skip" and not insider_override:
                    skip_retryable = decision.reason in _RETRYABLE_SKIP_REASONS
                elif final_action == "buy" and breaches_concentration_limit(
                    d.ticker, final_notional, broker, config
                ):
                    final_action = "skip"
                    final_reason = "would exceed MAX_POSITION_CONCENTRATION_PCT"
                    skip_retryable = True
                elif final_action == "sell" and not broker.has_open_position(d.ticker):
                    # Live check, not the evaluate_disclosures-time snapshot -- see
                    # strategy.py's sell branch for why. Retryable: a position from
                    # an unrelated later buy could still make this same disclosure
                    # sellable in a future run, right up until it ages out of
                    # LOOKBACK_DAYS and stops being fetched at all.
                    final_action = "skip"
                    final_reason = "no open position to sell"
                    skip_retryable = True

            decisions_log.log_decision(
                representative=d.representative,
                ticker=d.ticker,
                transaction_type=d.transaction_type,
                transaction_date=d.transaction_date,
                raw_range=d.raw_range,
                filed_date=d.filed_date,
                decision=final_action,
                reason=final_reason,
                insider_override=insider_override,
                insider_detail=insider_detail,
            )

            if final_action == "skip":
                logger.info("SKIP %s: %s", d.ticker, final_reason)
                if not skip_retryable:
                    store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            final_side = OrderSide.BUY if final_action == "buy" else OrderSide.SELL

            if final_action == "buy":
                logger.info(
                    "BUY %s ~$%.2f (mirroring %s, disclosed range %s, filed %s)%s",
                    d.ticker, final_notional, d.representative, d.raw_range, d.filed_date,
                    " [INSIDER OVERRIDE]" if insider_override else "",
                )
            else:
                logger.info(
                    "SELL/close position %s (mirroring %s, filed %s)%s",
                    d.ticker, d.representative, d.filed_date,
                    " [INSIDER OVERRIDE]" if insider_override else "",
                )

            if config.dry_run:
                store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            try:
                if final_action == "buy":
                    broker.submit_market_order(d.ticker, final_notional, final_side)
                else:
                    broker.close_position(d.ticker)
            except Exception:
                logger.exception("Order failed for %s, skipping", d.ticker)
                continue

            store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())
    finally:
        store.close()


if __name__ == "__main__":
    run()
