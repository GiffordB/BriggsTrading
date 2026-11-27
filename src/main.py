"""Dispatcher: `python -m src.main research` runs the nightly Scout->Ideas->
Quant->Skeptic->Rank pipeline (src/research_pipeline.py). `python -m src.main
execute` runs the morning step that acts on last night's shortlist
(src/execution.py). See playbook.md for the overall cadence.
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("research", "execute"):
        print("Usage: python -m src.main [research|execute]", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "research":
        from . import research_pipeline

        logger.info("Starting nightly research run")
        research_pipeline.run()
    else:
        from . import execution
        from .config import Config

        config = Config()
        if config.dry_run:
            logger.warning("Running in DRY_RUN mode: no orders will be submitted")
        if config.alpaca_paper:
            logger.info("Trading against Alpaca PAPER account")
        else:
            logger.warning("Trading against Alpaca LIVE account with REAL MONEY")

        logger.info("Starting morning execution run")
        execution.run()


if __name__ == "__main__":
    main()
