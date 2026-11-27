import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "decisions_log.jsonl"

# Keep the log from growing forever -- each run adds at most a handful of
# lines, so this is years of history before it matters.
MAX_LINES = 5000


def log_decision(
    symbol: str,
    strategy_name: str,
    direction: str,
    decision: str,
    reason: str,
    notional: float = 0.0,
    predicted_r: float = 0.0,
    score: float = 0.0,
) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "strategy_name": strategy_name,
        "direction": direction,
        "notional": notional,
        "predicted_r": predicted_r,
        "score": score,
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
