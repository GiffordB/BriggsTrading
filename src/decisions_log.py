import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "decisions_log.jsonl"

# Keep the log from growing forever -- each bot run can add at most a
# handful of lines, so this is years of history before it matters.
MAX_LINES = 5000


def log_decision(
    insider_name: str,
    role: str,
    ticker: str,
    transaction_type: str,
    filed_date: str,
    decision: str,
    reason: str,
    transaction_date: str = "",
    shares: float = 0.0,
    price_per_share: float = 0.0,
    notional: float = 0.0,
) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "insider_name": insider_name,
        "role": role,
        "ticker": ticker,
        "transaction_type": transaction_type,
        "transaction_date": transaction_date,
        "filed_date": filed_date,
        "shares": shares,
        "price_per_share": price_per_share,
        "notional": notional,
        "decision": decision,
        "reason": reason,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

    _trim_if_needed()


def _trim_if_needed() -> None:
    if not LOG_PATH.exists():
        return
    lines = LOG_PATH.read_text().splitlines()
    if len(lines) > MAX_LINES:
        LOG_PATH.write_text("\n".join(lines[-MAX_LINES:]) + "\n")
