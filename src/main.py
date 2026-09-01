import logging
from datetime import datetime, timezone

from . import decisions_log
from .alpaca_client import AlpacaClient
from .config import Config
from .quiver_client import QuiverClient
from .risk_guard import assess_risk, breaches_concentration_limit
from .state import SeenTradesStore
from .strategy import evaluate_disclosures

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

        decisions = evaluate_disclosures(new_disclosures, config, broker)

        for decision in decisions:
            d = decision.disclosure
            final_action = decision.action
            final_reason = decision.reason
            skip_retryable = False

            if decision.action == "skip":
                skip_retryable = decision.reason in _RETRYABLE_SKIP_REASONS
            elif risk_status.halted:
                final_action = "skip"
                final_reason = "blocked by risk guard: " + "; ".join(risk_status.reasons)
                skip_retryable = True
            elif decision.action == "buy" and breaches_concentration_limit(
                d.ticker, decision.notional, broker, config
            ):
                final_action = "skip"
                final_reason = "would exceed MAX_POSITION_CONCENTRATION_PCT"
                skip_retryable = True

            decisions_log.log_decision(
                representative=d.representative,
                ticker=d.ticker,
                transaction_type=d.transaction_type,
                raw_range=d.raw_range,
                filed_date=d.filed_date,
                decision=final_action,
                reason=final_reason,
            )

            if final_action == "skip":
                logger.info("SKIP %s: %s", d.ticker, final_reason)
                if not skip_retryable:
                    store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            if final_action == "buy":
                logger.info(
                    "BUY %s ~$%.2f (mirroring %s, disclosed range %s, filed %s)",
                    d.ticker, decision.notional, d.representative, d.raw_range, d.filed_date,
                )
            else:
                logger.info(
                    "SELL/close position %s (mirroring %s, filed %s)",
                    d.ticker, d.representative, d.filed_date,
                )

            if config.dry_run:
                store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            try:
                if final_action == "buy":
                    broker.submit_market_order(d.ticker, decision.notional, decision.side)
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
