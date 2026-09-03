import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_HOLDINGS_PATH = "data/real_holdings.json"


@dataclass
class RealHoldingsStore:
    """Manual tracker for the user's own real (non-paper) investments -- e.g.
    Schwab or Empower Retirement accounts the bot has no API access to -- so
    their performance can be compared against the paper bot's.

    Persisted as JSON in the BriggsTrading repo via the GitHub Contents API,
    since the dashboard runs as a stateless web service with no database of
    its own. Same pattern already used for the decisions audit log, just
    read+write instead of append-only.
    """

    github_pat: str
    github_repo: str
    path: str = _HOLDINGS_PATH

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_pat:
            headers["Authorization"] = f"Bearer {self.github_pat}"
        return headers

    def _contents_url(self) -> str:
        return f"{_API_BASE}/repos/{self.github_repo}/contents/{self.path}"

    def _get_file(self) -> tuple[list[dict], str | None]:
        """Returns (holdings, sha). sha is None if the file doesn't exist yet."""
        resp = requests.get(self._contents_url(), headers=self._headers(), timeout=10)
        if resp.status_code == 404:
            return [], None
        resp.raise_for_status()
        payload = resp.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        holdings = json.loads(content) if content.strip() else []
        return holdings, payload["sha"]

    def _put_file(self, holdings: list[dict], sha: str | None, message: str) -> None:
        body = {
            "message": message,
            "content": base64.b64encode(
                json.dumps(holdings, indent=2).encode("utf-8")
            ).decode("utf-8"),
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(
            self._contents_url(), headers=self._headers(), json=body, timeout=10
        )
        resp.raise_for_status()

    def list_holdings(self) -> list[dict]:
        if not self.github_pat:
            logger.warning("GITHUB_PAT not set -- real holdings tracker is read-only/disabled")
            return []
        try:
            holdings, _ = self._get_file()
            return holdings
        except Exception:
            logger.exception("Could not read real holdings from GitHub")
            return []

    def add_holding(
        self,
        ticker: str,
        shares: float,
        cost_per_share: float,
        account: str,
        manual_price: float | None = None,
        notes: str = "",
    ) -> dict:
        holdings, sha = self._get_file()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ticker": ticker.strip().upper(),
            "shares": shares,
            "cost_per_share": cost_per_share,
            "account": account.strip(),
            "manual_price": manual_price,
            "notes": notes.strip(),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        holdings.append(entry)
        self._put_file(holdings, sha, f"Add real holding: {entry['ticker']} ({entry['account']})")
        return entry

    def update_holding(self, holding_id: str, **fields) -> dict | None:
        holdings, sha = self._get_file()
        updated = None
        for h in holdings:
            if h["id"] == holding_id:
                for key in ("ticker", "shares", "cost_per_share", "account", "manual_price", "notes"):
                    if key in fields and fields[key] is not None:
                        h[key] = fields[key].strip().upper() if key == "ticker" else fields[key]
                updated = h
                break
        if updated is None:
            return None
        self._put_file(holdings, sha, f"Update real holding: {updated['ticker']}")
        return updated

    def delete_holding(self, holding_id: str) -> bool:
        holdings, sha = self._get_file()
        remaining = [h for h in holdings if h["id"] != holding_id]
        if len(remaining) == len(holdings):
            return False
        self._put_file(remaining, sha, f"Remove real holding {holding_id}")
        return True
