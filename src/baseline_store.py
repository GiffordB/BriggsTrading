import base64
import json
import time
from dataclasses import dataclass

import requests

_API_BASE = "https://api.github.com"
_BASELINE_PATH = "data/strategy_baseline.json"


@dataclass
class BaselineStore:
    """Marks the equity and date this strategy's track record should be
    measured from -- e.g. right after liquidating everything from a prior
    approach, so the dashboard's "return since baseline" isn't muddied by
    history that has nothing to do with the current strategy.

    Persisted as a single JSON object in the repo via the GitHub Contents
    API, same mechanism as the Real Holdings tracker -- the dashboard has no
    database of its own.
    """

    github_pat: str
    github_repo: str
    path: str = _BASELINE_PATH

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_pat:
            headers["Authorization"] = f"Bearer {self.github_pat}"
        return headers

    def _contents_url(self) -> str:
        return f"{_API_BASE}/repos/{self.github_repo}/contents/{self.path}"

    def get_baseline(self) -> dict | None:
        if not self.github_pat:
            return None
        resp = requests.get(self._contents_url(), headers=self._headers(), timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(content) if content.strip() else None

    def set_baseline(self, equity: float, note: str = "") -> dict:
        sha = None
        resp = requests.get(self._contents_url(), headers=self._headers(), timeout=10)
        if resp.status_code == 200:
            sha = resp.json()["sha"]
        elif resp.status_code != 404:
            resp.raise_for_status()

        entry = {
            "starting_equity": equity,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note.strip(),
        }
        body = {
            "message": f"Set strategy baseline: ${equity:,.2f}",
            "content": base64.b64encode(json.dumps(entry, indent=2).encode("utf-8")).decode("utf-8"),
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(self._contents_url(), headers=self._headers(), json=body, timeout=10)
        resp.raise_for_status()
        return entry
