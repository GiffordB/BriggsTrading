import time

import requests
from flask import Flask, jsonify, render_template

from src.alpaca_client import AlpacaClient
from src.config import Config
from src.metrics import compute_metrics
from src.quiver_client import QuiverClient
from src.risk_guard import assess_risk

app = Flask(__name__)

config = Config()
broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
quiver = QuiverClient(config.quiver_api_token)

DECISIONS_LOG_URL = (
    "https://raw.githubusercontent.com/GiffordB/BriggsTrading/main/data/decisions_log.jsonl"
)

_disclosures_cache: dict = {"data": [], "fetched_at": 0.0}
_DISCLOSURES_TTL_SECONDS = 300

_decisions_cache: dict = {"data": [], "fetched_at": 0.0}
_DECISIONS_TTL_SECONDS = 300


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
                import json

                entries = [json.loads(line) for line in lines[-limit:]]
                _decisions_cache["data"] = list(reversed(entries))
            else:
                _decisions_cache["data"] = []
        except Exception:
            _decisions_cache["data"] = []
        _decisions_cache["fetched_at"] = now
    return _decisions_cache["data"]


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
        history = broker.get_portfolio_history(period="3M", timeframe="1D")
        m = compute_metrics(history, positions)
        metrics = {
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "cagr_pct": m.cagr_pct,
            "open_position_win_rate_pct": m.open_position_win_rate_pct,
            "num_data_points": m.num_data_points,
        }
    except Exception as exc:
        metrics = {"error": str(exc)}

    return jsonify(
        {
            "account": account,
            "positions": positions,
            "orders": orders,
            "disclosures": disclosures,
            "decisions": decisions,
            "risk": risk,
            "metrics": metrics,
            "mode": {"paper": config.alpaca_paper, "dry_run": config.dry_run},
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
