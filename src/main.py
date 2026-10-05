import logging
from datetime import datetime, timezone

from . import decisions_log
from .alpaca_client import AlpacaClient
from .config import Config
from .risk_guard import assess_risk, breaches_concentration_limit
from .sec_edgar_client import SECEdgarClient
from .state import SeenTradesStore
from .strategy import evaluate_insider_filings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)

# Skip reasons that are about run-specific or portfolio-state-specific capacity
# rather than something inherent to the filing -- these should be retried on
# a later run rather than permanently marked "seen".
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

        filings = sec_edgar.scan_recent_form4_filings(
            config.lookback_days, config.max_insider_filings_per_run
        )
        logger.info(
            "Found %d directional (buy/sell) insider filings from the last %d days",
            len(filings), config.lookback_days,
        )

        new_filings = [f for f in filings if not store.has_seen(f.dedupe_key)]
        logger.info("%d filings are new (not previously acted on)", len(new_filings))

        decisions = evaluate_insider_filings(new_filings, config, broker)

        # Execute buys before sells: if two different insiders' filings for the
        # same ticker land in one run (one buying, one selling), this ensures a
        # position created by this run's own buy is visible to this run's sell
        # check below, rather than the sell always finding nothing to sell just
        # because it happened to be evaluated first.
        decisions = sorted(decisions, key=lambda dec: dec.action != "buy")

        for decision in decisions:
            f = decision.filing
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
                f.ticker, decision.notional, broker, config
            ):
                final_action = "skip"
                final_reason = "would exceed MAX_POSITION_CONCENTRATION_PCT"
                skip_retryable = True
            elif decision.action == "sell" and not broker.has_open_position(f.ticker):
                # Live check, not the evaluate_insider_filings-time snapshot --
                # see strategy.py's sell branch for why. Retryable: a position
                # from an unrelated later buy could still make this same
                # filing sellable in a future run, right up until it ages out
                # of LOOKBACK_DAYS and stops being fetched at all.
                final_action = "skip"
                final_reason = "no open position to sell"
                skip_retryable = True

            role = "officer" if f.is_officer else "director" if f.is_director else "insider"

            decisions_log.log_decision(
                insider_name=f.insider_name,
                role=role,
                ticker=f.ticker,
                transaction_type=f.transaction_type,
                transaction_date=f.transaction_date,
                filed_date=f.filed_date,
                shares=f.shares,
                price_per_share=f.price_per_share,
                notional=f.notional,
                decision=final_action,
                reason=final_reason,
            )

            if final_action == "skip":
                logger.info("SKIP %s: %s", f.ticker, final_reason)
                if not skip_retryable:
                    store.mark_seen(f.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            if final_action == "buy":
                logger.info(
                    "BUY %s ~$%.2f (mirroring %s %s, insider trade value $%.2f, filed %s)",
                    f.ticker, decision.notional, role, f.insider_name, f.notional, f.filed_date,
                )
            else:
                logger.info(
                    "SELL/close position %s (mirroring %s %s, filed %s)",
                    f.ticker, role, f.insider_name, f.filed_date,
                )

            if config.dry_run:
                store.mark_seen(f.dedupe_key, datetime.now(timezone.utc).isoformat())
                continue

            try:
                if final_action == "buy":
                    broker.submit_market_order(f.ticker, decision.notional, decision.side)
                else:
                    broker.close_position(f.ticker)
            except Exception:
                logger.exception("Order failed for %s, skipping", f.ticker)
                continue

            store.mark_seen(f.dedupe_key, datetime.now(timezone.utc).isoformat())
    finally:
        store.close()


if __name__ == "__main__":
    run()
