import logging
from datetime import datetime, timezone

from alpaca.trading.enums import OrderSide

from .alpaca_client import AlpacaClient
from .config import Config
from .quiver_client import QuiverClient
from .state import SeenTradesStore
from .strategy import plan_orders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)


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
        disclosures = quiver.fetch_recent_congress_trades(config.lookback_days)
        logger.info("Fetched %d disclosures from the last %d days", len(disclosures), config.lookback_days)

        new_disclosures = [d for d in disclosures if not store.has_seen(d.dedupe_key)]
        logger.info("%d disclosures are new (not previously acted on)", len(new_disclosures))

        planned_orders = plan_orders(new_disclosures, config, broker)
        logger.info("Planned %d orders after applying strategy filters", len(planned_orders))

        for planned in planned_orders:
            d = planned.disclosure
            if planned.side == OrderSide.BUY:
                logger.info(
                    "BUY %s ~$%.2f (mirroring %s, disclosed range %s, filed %s)",
                    d.ticker, planned.notional, d.representative, d.raw_range, d.filed_date,
                )
            else:
                logger.info(
                    "SELL/close position %s (mirroring %s, filed %s)",
                    d.ticker, d.representative, d.filed_date,
                )

            if config.dry_run:
                continue

            try:
                if planned.side == OrderSide.BUY:
                    broker.submit_market_order(d.ticker, planned.notional, OrderSide.BUY)
                else:
                    broker.close_position(d.ticker)
            except Exception:
                logger.exception("Order failed for %s, skipping", d.ticker)
                continue

            store.mark_seen(d.dedupe_key, datetime.now(timezone.utc).isoformat())

        if config.dry_run:
            # Still record disclosures as seen so a later real run doesn't
            # suddenly fire on everything the dry run already logged.
            for planned in planned_orders:
                store.mark_seen(planned.disclosure.dedupe_key, datetime.now(timezone.utc).isoformat())
    finally:
        store.close()


if __name__ == "__main__":
    run()
