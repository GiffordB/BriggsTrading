from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

QUIVER_BASE_URL = "https://api.quiverquant.com/beta"

# Congress disclosures report amounts as ranges (e.g. "$1,001 - $15,000").
# We key off the low end so MIN_TRADE_AMOUNT filters conservatively.
_RANGE_LOW = {
    "$1,001 - $15,000": 1001,
    "$15,001 - $50,000": 15001,
    "$50,001 - $100,000": 50001,
    "$100,001 - $250,000": 100001,
    "$250,001 - $500,000": 250001,
    "$500,001 - $1,000,000": 500001,
    "$1,000,001 - $5,000,000": 1000001,
    "$5,000,001 - $25,000,000": 5000001,
}


@dataclass(frozen=True)
class Disclosure:
    trade_id: str
    representative: str
    ticker: str
    transaction_type: str
    transaction_date: str
    filed_date: str
    amount_low: float
    raw_range: str

    @property
    def dedupe_key(self) -> str:
        return f"{self.representative}|{self.ticker}|{self.transaction_date}|{self.transaction_type}|{self.raw_range}"


class QuiverClient:
    def __init__(self, api_token: str):
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
        )

    def fetch_recent_congress_trades(self, lookback_days: int) -> list[Disclosure]:
        """Fetch congress trading disclosures filed in the last `lookback_days` days.

        Uses Quiver's /beta/live/congresstrading endpoint. Quiver's API has changed
        shape before -- if this starts returning errors, check the current schema
        at https://api.quiverquant.com/docs/ and adjust the field names below.
        """
        resp = self._session.get(f"{QUIVER_BASE_URL}/live/congresstrading", timeout=30)
        resp.raise_for_status()
        rows = resp.json()

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        disclosures = []
        for row in rows:
            filed_date_str = row.get("Filed") or row.get("ReportDate")
            if not filed_date_str:
                continue
            try:
                filed_date = datetime.fromisoformat(filed_date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if filed_date.tzinfo is None:
                filed_date = filed_date.replace(tzinfo=timezone.utc)
            if filed_date < cutoff:
                continue

            ticker = row.get("Ticker")
            if not ticker:
                continue

            raw_range = row.get("Range") or row.get("Amount") or ""
            amount_low = _RANGE_LOW.get(raw_range, 0)

            disclosures.append(
                Disclosure(
                    trade_id=str(row.get("_id") or row.get("ID") or ""),
                    representative=row.get("Representative") or row.get("Senator") or "unknown",
                    ticker=ticker,
                    transaction_type=row.get("Transaction", "unknown"),
                    transaction_date=row.get("TransactionDate", ""),
                    filed_date=filed_date_str,
                    amount_low=amount_low,
                    raw_range=raw_range,
                )
            )
        return disclosures
