import time

from flask import Flask, jsonify, render_template

from src.alpaca_client import AlpacaClient
from src.config import Config
from src.quiver_client import QuiverClient

app = Flask(__name__)

config = Config()
broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
quiver = QuiverClient(config.quiver_api_token)

_disclosures_cache: dict = {"data": [], "fetched_at": 0.0}
_DISCLOSURES_TTL_SECONDS = 300


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

    return jsonify(
        {
            "account": account,
            "positions": positions,
            "orders": orders,
            "disclosures": disclosures,
            "mode": {"paper": config.alpaca_paper, "dry_run": config.dry_run},
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
