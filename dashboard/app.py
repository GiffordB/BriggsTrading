import hmac
import json
import logging
import time

import requests
from alpaca.trading.enums import OrderSide
from flask import Flask, Response, jsonify, render_template, request

from src.alpaca_client import AlpacaClient
from src.config import Config
from src.metrics import compute_metrics
from src.news_sentiment import is_bad_news
from src.quiver_client import QuiverClient
from src.real_holdings_store import RealHoldingsStore
from src.risk_guard import assess_risk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

config = Config()
broker = AlpacaClient(config.alpaca_api_key, config.alpaca_secret_key, config.alpaca_paper)
quiver = QuiverClient(config.quiver_api_token)
real_holdings_store = RealHoldingsStore(config.github_pat, config.github_repo)

if not config.github_pat:
    logger.warning(
        "GITHUB_PAT is not set -- the Real Holdings tracker is disabled (nothing "
        "to compare against the paper bot will be shown)."
    )

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

_disclosure_price_cache: dict = {"data": {}, "fetched_at": 0.0}
_DISCLOSURE_PRICE_TTL_SECONDS = 900
# Covers a disclosure's LOOKBACK_DAYS window plus the full 45-day legal filing
# delay, with a buffer -- transaction_date can be up to ~45 days before the
# disclosure was even fetched.
_DISCLOSURE_PRICE_LOOKBACK_DAYS = 120


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


def _price_on_or_before(series: list[dict], target_date: str) -> float | None:
    candidates = [p["close"] for p in series if p["date"] <= target_date]
    return candidates[-1] if candidates else None


def _attach_transaction_prices(rows: list[dict]) -> None:
    """Adds an approximate 'transaction_price' to each row -- Congress never
    discloses the actual execution price (only a dollar range), so this is the
    stock's closing price on-or-before the disclosed transaction date, not a
    real fill price. Mutates rows in place; missing transaction_date (older
    audit log entries logged before this field existed) just get None."""
    tickers = sorted({r["ticker"] for r in rows if r.get("transaction_date")})
    now = time.time()
    if (
        tickers != _disclosure_price_cache.get("tickers")
        or now - _disclosure_price_cache["fetched_at"] > _DISCLOSURE_PRICE_TTL_SECONDS
    ):
        try:
            _disclosure_price_cache["data"] = broker.get_price_history(
                tickers, lookback_days=_DISCLOSURE_PRICE_LOOKBACK_DAYS
            )
        except Exception:
            logger.exception("Could not fetch historical prices for disclosures/audit log")
            _disclosure_price_cache["data"] = {}
        _disclosure_price_cache["tickers"] = tickers
        _disclosure_price_cache["fetched_at"] = now

    price_history = _disclosure_price_cache["data"]
    for row in rows:
        # Normalize to YYYY-MM-DD in case the source includes a time component.
        transaction_date = (row.get("transaction_date") or "")[:10]
        series = price_history.get(row["ticker"]) if transaction_date else None
        row["transaction_price"] = _price_on_or_before(series, transaction_date) if series else None


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


def _compute_real_holdings() -> dict:
    """Enriches the user's manually-entered real holdings with a current price --
    live from Alpaca where the ticker is tradable there, otherwise falling back
    to the holding's own manual_price (e.g. SPACX / other private-company stock
    Alpaca has no quote for at all). Also rolls up an overall return % so it can
    be compared, directionally, against the paper bot's."""
    holdings = real_holdings_store.list_holdings()
    tickers = sorted({h["ticker"] for h in holdings})

    live_prices: dict[str, float] = {}
    if tickers:
        try:
            history = broker.get_price_history(tickers, lookback_days=5)
            for ticker, series in history.items():
                if series:
                    live_prices[ticker] = series[-1]["close"]
        except Exception:
            logger.exception("Could not fetch live prices for real holdings")

    enriched = []
    total_cost = 0.0
    total_value = 0.0
    total_value_known = True
    for h in holdings:
        cost_basis = h["shares"] * h["cost_per_share"]
        total_cost += cost_basis

        current_price = live_prices.get(h["ticker"])
        price_source = "live" if current_price is not None else None
        if current_price is None and h.get("manual_price") is not None:
            current_price = h["manual_price"]
            price_source = "manual"

        market_value = h["shares"] * current_price if current_price is not None else None
        gain_pct = (
            (market_value / cost_basis - 1) * 100 if market_value is not None and cost_basis else None
        )
        if market_value is not None:
            total_value += market_value
        else:
            total_value_known = False

        enriched.append(
            {
                **h,
                "cost_basis": cost_basis,
                "current_price": current_price,
                "price_source": price_source,
                "market_value": market_value,
                "gain_pct": gain_pct,
            }
        )

    overall_return_pct = (
        (total_value / total_cost - 1) * 100 if total_value_known and total_cost else None
    )

    return {
        "holdings": enriched,
        "totals": {
            "cost_basis": total_cost,
            "market_value": total_value if total_value_known else None,
            "return_pct": overall_return_pct,
        },
    }


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
        _attach_transaction_prices(disclosures + decisions)
    except Exception:
        logger.exception("Could not attach transaction prices")

    try:
        news_alerts = _news_for_positions(positions)
    except Exception:
        logger.exception("Could not fetch news")
        news_alerts = []

    bad_news_alerts = [a for a in news_alerts if is_bad_news(a.get("headline", ""))]

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

    bot_return_pct = None
    if len(equity_curve) >= 2 and equity_curve[0]["equity"]:
        bot_return_pct = (equity_curve[-1]["equity"] / equity_curve[0]["equity"] - 1) * 100

    try:
        real_holdings = _compute_real_holdings()
        real_holdings["bot_return_pct"] = bot_return_pct
    except Exception:
        logger.exception("Could not compute real holdings")
        real_holdings = {"holdings": [], "totals": {}, "bot_return_pct": bot_return_pct}

    return jsonify(
        {
            "account": account,
            "positions": positions,
            "orders": orders,
            "disclosures": disclosures,
            "decisions": decisions,
            "news_alerts": news_alerts,
            "bad_news_alerts": bad_news_alerts,
            "price_history": price_history,
            "equity_curve": equity_curve,
            "risk": risk,
            "metrics": metrics,
            "real_holdings": real_holdings,
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


@app.route("/api/buy", methods=["POST"])
def api_buy():
    """Manual buy -- primarily for reversing an insider-override sell (see the
    'Reverse' button on flagged Audit Log rows), but usable for any ticker."""
    data = request.get_json(force=True, silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"success": False, "error": "ticker is required"}), 400

    try:
        notional = float(data.get("notional"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "notional must be a number"}), 400
    if notional <= 0:
        return jsonify({"success": False, "error": "notional must be positive"}), 400

    try:
        if not broker.is_tradable(ticker):
            return jsonify({"success": False, "error": f"{ticker} is not tradable on Alpaca"}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"Could not verify tradability: {exc}"}), 502

    try:
        broker.submit_market_order(ticker, notional, OrderSide.BUY)
    except Exception as exc:
        logger.exception("Manual buy failed for %s", ticker)
        return jsonify({"success": False, "error": str(exc)}), 502

    logger.warning("Manual buy triggered from dashboard for %s (~$%.2f)", ticker, notional)
    return jsonify({"success": True, "ticker": ticker})


@app.route("/api/real-holdings", methods=["POST"])
def api_real_holdings_add():
    if not config.github_pat:
        return jsonify({"success": False, "error": "GITHUB_PAT is not configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    ticker = (data.get("ticker") or "").strip()
    account = (data.get("account") or "").strip()
    try:
        shares = float(data.get("shares"))
        cost_per_share = float(data.get("cost_per_share"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "shares and cost_per_share must be numbers"}), 400
    if not ticker or not account:
        return jsonify({"success": False, "error": "ticker and account are required"}), 400

    manual_price = data.get("manual_price")
    try:
        manual_price = float(manual_price) if manual_price not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "manual_price must be a number"}), 400

    try:
        entry = real_holdings_store.add_holding(
            ticker=ticker,
            shares=shares,
            cost_per_share=cost_per_share,
            account=account,
            manual_price=manual_price,
            notes=(data.get("notes") or "").strip(),
        )
    except Exception as exc:
        logger.exception("Could not add real holding")
        return jsonify({"success": False, "error": str(exc)}), 502

    return jsonify({"success": True, "holding": entry})


@app.route("/api/real-holdings/<holding_id>", methods=["PUT"])
def api_real_holdings_update(holding_id: str):
    if not config.github_pat:
        return jsonify({"success": False, "error": "GITHUB_PAT is not configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    fields: dict = {}
    for key in ("ticker", "account", "notes"):
        if key in data:
            fields[key] = data[key]
    for key in ("shares", "cost_per_share", "manual_price"):
        if key in data:
            try:
                fields[key] = float(data[key]) if data[key] not in (None, "") else None
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": f"{key} must be a number"}), 400

    try:
        updated = real_holdings_store.update_holding(holding_id, **fields)
    except Exception as exc:
        logger.exception("Could not update real holding %s", holding_id)
        return jsonify({"success": False, "error": str(exc)}), 502

    if updated is None:
        return jsonify({"success": False, "error": "No such holding"}), 404
    return jsonify({"success": True, "holding": updated})


@app.route("/api/real-holdings/<holding_id>", methods=["DELETE"])
def api_real_holdings_delete(holding_id: str):
    if not config.github_pat:
        return jsonify({"success": False, "error": "GITHUB_PAT is not configured"}), 503

    try:
        deleted = real_holdings_store.delete_holding(holding_id)
    except Exception as exc:
        logger.exception("Could not delete real holding %s", holding_id)
        return jsonify({"success": False, "error": str(exc)}), 502

    if not deleted:
        return jsonify({"success": False, "error": "No such holding"}), 404
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
