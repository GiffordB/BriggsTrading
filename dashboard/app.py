import hmac
import json
import logging
import time

import requests
from flask import Flask, Response, jsonify, render_template, request

from src.alpaca_client import AlpacaClient
from src.config import Config
from src.metrics import compute_metrics
from src.quiver_client import QuiverClient
from src.risk_guard import assess_risk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

config = Config()
broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
quiver = QuiverClient(config.quiver_api_token)

if not config.dashboard_username or not config.dashboard_password:
    logger.warning(
        "DASHBOARD_USERNAME / DASHBOARD_PASSWORD are not set -- this dashboard, "
        "including the manual sell button, is running with NO LOGIN. Set both "
        "before deploying anywhere reachable by anyone but you."
    )

DECISIONS_LOG_URL = (
    "https://raw.githubusercontent.com/GiffordB/BriggsTrading/main/data/decisions_log.jsonl"
)

_disclosures_cache: dict = {"data": [], "fetched_at": 0.0}
_DISCLOSURES_TTL_SECONDS = 300

_decisions_cache: dict = {"data": [], "fetched_at": 0.0}
_DECISIONS_TTL_SECONDS = 300

_news_cache: dict = {"data": [], "fetched_at": 0.0}
_NEWS_TTL_SECONDS = 300

_price_history_cache: dict = {"data": {}, "fetched_at": 0.0}
_PRICE_HISTORY_TTL_SECONDS = 900  # daily bars don't change intraday; cache generously


@app.before_request
def require_auth():
    if not config.dashboard_username or not config.dashboard_password:
        return  # not configured -- see startup warning above
    auth = request.authorization
    valid = (
        auth is not None
        and hmac.compare_digest(auth.username, config.dashboard_username)
        and hmac.compare_digest(auth.password, config.dashboard_password)
    )
    if not valid:
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="BriggsTrading Dashboard"'},
        )


def _recent_disclosures() -> list[dict]:
    now = time.time()
    if now - _disclosures_cache["fetched_at"] > _DISCLOSURES_TTL_SECONDS:
        disclosures = quiver.fetch_recent_congress_trades(config.lookback_days)
        _disclosures_cache["data"] = [
            {
                "representative": d.representative,
                "ticker": d.ticker,
                "transaction_type": d.transaction_type,
                "transaction_date": d.transaction_date,
                "filed_date": d.filed_date,
                "raw_range": d.raw_range,
            }
            for d in sorted(disclosures, key=lambda d: d.filed_date, reverse=True)
        ]
        _disclosures_cache["fetched_at"] = now
    return _disclosures_cache["data"]


def _recent_decisions(limit: int = 30) -> list[dict]:
    """Audit log written by the bot each run and committed back to the repo --
    fetched here from GitHub's raw content endpoint since this dashboard runs as
    a separate service with no direct access to the GitHub Actions runner."""
    now = time.time()
    if now - _decisions_cache["fetched_at"] > _DECISIONS_TTL_SECONDS:
        try:
            resp = requests.get(DECISIONS_LOG_URL, timeout=10)
            if resp.status_code == 200:
                lines = [line for line in resp.text.splitlines() if line.strip()]
                entries = [json.loads(line) for line in lines[-limit:]]
                _decisions_cache["data"] = list(reversed(entries))
            else:
                _decisions_cache["data"] = []
        except Exception:
            _decisions_cache["data"] = []
        _decisions_cache["fetched_at"] = now
    return _decisions_cache["data"]


def _news_for_positions(positions: list[dict]) -> list[dict]:
    """Recent news for currently-held tickers only -- this is a visibility layer,
    not an auto-sell trigger, so it never touches order placement on its own."""
    tickers = sorted({p["symbol"] for p in positions})
    now = time.time()
    if tickers != _news_cache.get("tickers") or now - _news_cache["fetched_at"] > _NEWS_TTL_SECONDS:
        _news_cache["data"] = broker.get_recent_news(tickers, config.news_lookback_days)
        _news_cache["tickers"] = tickers
        _news_cache["fetched_at"] = now
    return _news_cache["data"]


def _price_history_for_positions(positions: list[dict]) -> dict[str, list[dict]]:
    """Daily price series per held ticker, for the sparkline next to each position --
    shows the trend behind today's snapshot, not just the point-in-time number."""
    tickers = sorted({p["symbol"] for p in positions})
    now = time.time()
    if (
        tickers != _price_history_cache.get("tickers")
        or now - _price_history_cache["fetched_at"] > _PRICE_HISTORY_TTL_SECONDS
    ):
        _price_history_cache["data"] = broker.get_price_history(tickers, lookback_days=90)
        _price_history_cache["tickers"] = tickers
        _price_history_cache["fetched_at"] = now
    return _price_history_cache["data"]


@app.route("/")
def index():
    return render_template("index.html", paper=config.alpaca_paper, dry_run=config.dry_run)


@app.route("/api/data")
def api_data():
    try:
        account = broker.get_account_summary()
    except Exception as exc:
        return jsonify({"error": f"Could not reach Alpaca: {exc}"}), 502

    try:
        positions = broker.list_positions()
    except Exception:
        positions = []

    try:
        orders = broker.list_recent_orders(limit=25)
    except Exception:
        orders = []

    try:
        disclosures = _recent_disclosures()
    except Exception:
        disclosures = []

    try:
        decisions = _recent_decisions()
    except Exception:
        decisions = []

    try:
        news_alerts = _news_for_positions(positions)
    except Exception:
        logger.exception("Could not fetch news")
        news_alerts = []

    try:
        price_history = _price_history_for_positions(positions)
    except Exception:
        logger.exception("Could not fetch price history")
        price_history = {}

    try:
        risk_status = assess_risk(broker, config)
        risk = {
            "halted": risk_status.halted,
            "reasons": risk_status.reasons,
            "drawdown_pct": risk_status.current_drawdown_pct * 100,
            "exposure_pct": risk_status.total_exposure_pct * 100,
        }
    except Exception as exc:
        risk = {"halted": None, "reasons": [f"Could not compute: {exc}"], "drawdown_pct": None, "exposure_pct": None}

    try:
        equity_curve = broker.get_portfolio_history(period="3M", timeframe="1D")
        m = compute_metrics(equity_curve, positions)
        metrics = {
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "cagr_pct": m.cagr_pct,
            "open_position_win_rate_pct": m.open_position_win_rate_pct,
            "num_data_points": m.num_data_points,
        }
    except Exception as exc:
        equity_curve = []
        metrics = {"error": str(exc)}

    return jsonify(
        {
            "account": account,
            "positions": positions,
            "orders": orders,
            "disclosures": disclosures,
            "decisions": decisions,
            "news_alerts": news_alerts,
            "price_history": price_history,
            "equity_curve": equity_curve,
            "risk": risk,
            "metrics": metrics,
            "mode": {"paper": config.alpaca_paper, "dry_run": config.dry_run},
        }
    )


@app.route("/api/sell", methods=["POST"])
def api_sell():
    data = request.get_json(force=True, silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"success": False, "error": "ticker is required"}), 400

    try:
        held_tickers = {p["symbol"] for p in broker.list_positions()}
    except Exception as exc:
        return jsonify({"success": False, "error": f"Could not verify positions: {exc}"}), 502

    if ticker not in held_tickers:
        return jsonify({"success": False, "error": f"No open position in {ticker}"}), 400

    try:
        broker.close_position(ticker)
    except Exception as exc:
        logger.exception("Manual sell failed for %s", ticker)
        return jsonify({"success": False, "error": str(exc)}), 502

    logger.warning("Manual sell triggered from dashboard for %s", ticker)
    return jsonify({"success": True, "ticker": ticker})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
