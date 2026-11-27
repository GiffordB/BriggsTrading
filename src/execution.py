"""Morning execution: reads last night's research output, manages any open
position that's run past its strategy's time-exit, and -- if the risk guard
allows it -- opens up to MAX_SETUPS_PER_MORNING new bracket-order positions
for symbols where a shortlisted strategy's anomaly actually fired last
night. "Nothing to do" is the common and expected result, not an error.
"""

import csv
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from . import decisions_log
from .alpaca_client import AlpacaClient
from .config import Config
from .ideas import CANDIDATE_STRATEGIES, StrategyDef
from .risk_guard import assess_risk, breaches_concentration_limit
from .state import PositionTracker

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"
TRADES_CSV = Path(__file__).resolve().parent.parent / "data" / "trades.csv"
TRADES_CSV_FIELDS = [
    "symbol", "strategy_name", "direction", "entry_date", "entry_price",
    "exit_date", "exit_price", "exit_reason", "qty", "predicted_r", "realized_r",
]


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _strategy_by_name(name: str) -> StrategyDef | None:
    return next((s for s in CANDIDATE_STRATEGIES if s.name == name), None)


def _append_trade_row(row: dict) -> None:
    TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not TRADES_CSV.exists()
    with open(TRADES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def manage_open_positions(broker: AlpacaClient, tracker: PositionTracker) -> None:
    """Force-closes any tracked position that has run past its strategy's
    max_holding_days without already hitting its bracket stop/target -- the
    one exit rule a bracket order can't enforce by itself."""
    today = date.today()
    for pos in tracker.list_open():
        symbol = pos["symbol"]
        if not broker.has_open_position(symbol):
            # Already closed by its own bracket stop/target; log it so
            # trades.csv has a realized result to compare against the
            # predicted R later.
            _append_trade_row(
                {
                    "symbol": symbol, "strategy_name": pos["strategy_name"],
                    "direction": pos["direction"], "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"], "exit_date": today.isoformat(),
                    "exit_price": "", "exit_reason": "stop_or_target",
                    "qty": pos["qty"], "predicted_r": pos["predicted_r"], "realized_r": "",
                }
            )
            tracker.remove(symbol)
            continue

        entry_date = date.fromisoformat(pos["entry_date"])
        if (today - entry_date).days < pos["max_holding_days"]:
            continue

        try:
            broker.close_position(symbol)
        except Exception:
            logger.exception("Time-exit close failed for %s", symbol)
            continue

        logger.info(
            "TIME EXIT %s (strategy=%s, held %d days)",
            symbol, pos["strategy_name"], (today - entry_date).days,
        )
        _append_trade_row(
            {
                "symbol": symbol, "strategy_name": pos["strategy_name"],
                "direction": pos["direction"], "entry_date": pos["entry_date"],
                "entry_price": pos["entry_price"], "exit_date": today.isoformat(),
                "exit_price": "", "exit_reason": "time", "qty": pos["qty"],
                "predicted_r": pos["predicted_r"], "realized_r": "",
            }
        )
        tracker.remove(symbol)


def run() -> None:
    config = Config()
    config.validate()
    broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
    tracker = PositionTracker()

    try:
        manage_open_positions(broker, tracker)

        risk_status = assess_risk(broker, config)
        logger.info(
            "Risk check: halt_new_entries=%s halt_everything=%s drawdown=%.1f%% "
            "exposure=%.1f%% daily_pl=%.1f%%",
            risk_status.halt_new_entries, risk_status.halt_everything,
            risk_status.current_drawdown_pct * 100, risk_status.total_exposure_pct * 100,
            risk_status.daily_pl_pct * 100,
        )
        for reason in risk_status.reasons:
            logger.warning("Risk guard: %s", reason)

        if risk_status.halt_new_entries:
            decisions_log.log_decision(
                symbol="-", strategy_name="-", direction="-", decision="no_trade",
                reason="blocked by risk guard: " + "; ".join(risk_status.reasons),
            )
            logger.info("No new entries this run (risk guard).")
            return

        shortlist = _load_json(RESEARCH_DIR / "shortlist.json")
        tonight = _load_json(RESEARCH_DIR / "tonight_signals.json")

        shortlist_by_anomaly: dict[str, list[dict]] = {}
        for entry in shortlist:
            shortlist_by_anomaly.setdefault(entry["anomaly_column"], []).append(entry)

        open_symbols = {p["symbol"] for p in tracker.list_open()}
        candidates = []
        for sig in tonight:
            if sig["symbol"] in open_symbols:
                continue
            for strat_entry in shortlist_by_anomaly.get(sig["anomaly"], []):
                candidates.append({**strat_entry, "symbol": sig["symbol"]})
        candidates.sort(key=lambda c: c["score"], reverse=True)

        if not candidates:
            decisions_log.log_decision(
                symbol="-", strategy_name="-", direction="-", decision="no_trade",
                reason="no shortlisted strategy's anomaly fired on any symbol last night",
            )
            logger.info("No trade today: nothing on the shortlist fired.")
            return

        account = broker.get_account_summary()
        equity = account["equity"]
        taken = 0

        for cand in candidates:
            if taken >= config.max_setups_per_morning:
                break
            symbol = cand["symbol"]
            strategy = _strategy_by_name(cand["name"])
            if strategy is None:
                continue

            if cand["direction"] == "short" and not config.allow_short:
                decisions_log.log_decision(
                    symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                    decision="skip", reason="ALLOW_SHORT is false",
                )
                continue

            try:
                if not broker.is_tradable(symbol):
                    decisions_log.log_decision(
                        symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                        decision="skip", reason="not tradable on Alpaca",
                    )
                    continue
            except Exception as exc:
                logger.warning("Could not verify tradability for %s: %s", symbol, exc)
                continue

            # Size from the stop distance: RISK_PER_TRADE_PCT of equity risked
            # over stop_pct, capped by a fraction of Kelly (from this
            # strategy's own backtested win rate / avg win / avg loss) and by
            # MAX_LEVERAGE -- see playbook.md.
            risk_dollars = equity * config.risk_per_trade_pct
            notional = risk_dollars / cand["stop_pct"]
            kelly_pct = cand.get("kelly_pct", 0.0)
            if kelly_pct > 0:
                kelly_notional = (equity * kelly_pct * config.kelly_fraction_cap) / cand["stop_pct"]
                notional = min(notional, kelly_notional)
            notional = min(notional, equity * config.max_leverage)
            if notional < 1:
                continue

            if breaches_concentration_limit(symbol, notional, broker, config):
                decisions_log.log_decision(
                    symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                    decision="skip", reason="would exceed MAX_POSITION_CONCENTRATION_PCT",
                )
                continue

            if config.dry_run:
                logger.info(
                    "[DRY RUN] Would enter %s %s ~$%.2f (strategy=%s, score=%.1f)",
                    cand["direction"], symbol, notional, cand["name"], cand["score"],
                )
                decisions_log.log_decision(
                    symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                    decision="dry_run", reason="DRY_RUN is set", notional=notional,
                    predicted_r=cand.get("mean_r", 0.0), score=cand["score"],
                )
                taken += 1
                continue

            try:
                price_history = broker.get_price_history([symbol], lookback_days=5)
                series = price_history.get(symbol, [])
                last_close = series[-1]["close"] if series else None
                if not last_close:
                    continue
                qty = max(1, int(notional / last_close))
                is_long = cand["direction"] == "long"
                stop_price = (
                    last_close * (1 - strategy.stop_pct)
                    if is_long
                    else last_close * (1 + strategy.stop_pct)
                )
                target_price = None
                if strategy.target_pct:
                    target_price = (
                        last_close * (1 + strategy.target_pct)
                        if is_long
                        else last_close * (1 - strategy.target_pct)
                    )
                broker.submit_bracket_order(
                    symbol=symbol, qty=qty, side=cand["direction"],
                    stop_loss_price=round(stop_price, 2),
                    take_profit_price=round(target_price, 2) if target_price else None,
                )
            except Exception:
                logger.exception("Order failed for %s, skipping", symbol)
                continue

            tracker.record_open(
                symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                entry_date=date.today().isoformat(), max_holding_days=strategy.max_holding_days,
                entry_price=last_close, stop_price=stop_price, target_price=target_price or 0.0,
                qty=qty, predicted_r=cand.get("mean_r", 0.0),
            )
            decisions_log.log_decision(
                symbol=symbol, strategy_name=cand["name"], direction=cand["direction"],
                decision="enter", reason=f"shortlisted score {cand['score']:.1f}",
                notional=qty * last_close, predicted_r=cand.get("mean_r", 0.0), score=cand["score"],
            )
            logger.info(
                "ENTER %s %s qty=%d ~$%.2f (strategy=%s)",
                cand["direction"], symbol, qty, qty * last_close, cand["name"],
            )
            taken += 1

        if taken == 0:
            logger.info("No trade today: all candidates were filtered out.")
    finally:
        tracker.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
