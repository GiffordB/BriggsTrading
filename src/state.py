import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "bot_state.db"


class PositionTracker:
    """Tracks which open Alpaca positions this bot opened, which strategy and
    exit rules they belong to, and when -- needed because a bracket order's
    stop/target are enforced by Alpaca itself, but the strategy's
    max_holding_days time-exit is not, so something has to remember when each
    position was opened to know when to force-close it."""

    def __init__(self, db_path: Path = DB_PATH):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS open_positions (
                symbol TEXT PRIMARY KEY,
                strategy_name TEXT,
                direction TEXT,
                entry_date TEXT,
                max_holding_days INTEGER,
                entry_price REAL,
                stop_price REAL,
                target_price REAL,
                qty REAL,
                predicted_r REAL
            )"""
        )
        self._conn.commit()

    def record_open(self, **fields) -> None:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        self._conn.execute(
            f"INSERT OR REPLACE INTO open_positions ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        self._conn.commit()

    def list_open(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM open_positions")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def remove(self, symbol: str) -> None:
        self._conn.execute("DELETE FROM open_positions WHERE symbol = ?", (symbol,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
