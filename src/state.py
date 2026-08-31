import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "seen_trades.db"


class SeenTradesStore:
    def __init__(self, db_path: Path = DB_PATH):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_trades (dedupe_key TEXT PRIMARY KEY, acted_at TEXT)"
        )
        self._conn.commit()

    def has_seen(self, dedupe_key: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_trades WHERE dedupe_key = ?", (dedupe_key,)
        )
        return cur.fetchone() is not None

    def mark_seen(self, dedupe_key: str, acted_at: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_trades (dedupe_key, acted_at) VALUES (?, ?)",
            (dedupe_key, acted_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
