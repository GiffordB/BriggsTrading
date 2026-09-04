"""Free, public SEC EDGAR data -- no API key or subscription needed.

Used as a free alternative to Quiver's paid Insider Trading tier: checks
whether a company's own executives/directors have recently filed a Form 4
reporting an open-market purchase of their own stock. Insiders must file
within 2 business days of the trade (vs. 30-45 days for Congress), so this
is a much timelier signal when it fires.

SEC asks that automated requests set a descriptive User-Agent identifying
the requester (https://www.sec.gov/os/webmaster-faq#developers). Set
SEC_EDGAR_USER_AGENT to something identifying this project and a real
contact if you have one, to stay in good standing with their fair-access
policy. This client also pauses briefly between requests to avoid hammering
their servers.
"""

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

SEC_BASE_URL = "https://www.sec.gov"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# The only transaction code that represents a genuine discretionary
# open-market purchase. Excludes grants/awards (A), option exercises (M),
# gifts (G), tax withholding (F), and other non-discretionary types that
# don't reflect an insider choosing to buy more stock.
_PURCHASE_CODE = "P"

# The sale-side equivalent -- a genuine discretionary open-market sale.
# Together with _PURCHASE_CODE, this is the "directional" pair used by
# most_recent_directional_transaction() for the insider-override check.
_SALE_CODE = "S"
_DIRECTIONAL_CODES = {_PURCHASE_CODE, _SALE_CODE}


def _parse_bool_flag(value: str | None) -> bool:
    """Handles both '1'/'0' and 'true'/'false' -- different filers' Form 4
    XML use different conventions for the same boolean fields."""
    return (value or "").strip().lower() in ("1", "true")


@dataclass(frozen=True)
class InsiderTransaction:
    ticker: str
    insider_name: str
    is_officer: bool
    is_director: bool
    transaction_code: str
    transaction_date: str
    shares: float
    price_per_share: float


class SECEdgarClient:
    def __init__(self, user_agent: str):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    def has_recent_insider_purchase(self, ticker: str, lookback_days: int) -> bool:
        """True if any insider filed a Form 4 open-market purchase (code 'P')
        for this ticker within the lookback window. Never raises -- any
        network/parsing failure just means "no confirmation found", not a
        crash of the whole bot run."""
        try:
            filings = self._recent_form4_filings(ticker, lookback_days)
        except Exception:
            logger.warning("Could not fetch SEC EDGAR Form 4 filings for %s", ticker, exc_info=True)
            return False

        for filing in filings:
            try:
                transactions = self._fetch_transactions(filing["directory_url"])
            except Exception:
                continue
            if any(t.transaction_code == _PURCHASE_CODE for t in transactions):
                return True
            time.sleep(0.15)  # be polite to SEC's servers between requests
        return False

    def most_recent_directional_transaction(
        self, ticker: str, lookback_days: int
    ) -> InsiderTransaction | None:
        """The single most recent genuine open-market insider buy (P) or sale
        (S) for this ticker within the lookback window, or None if there isn't
        one. Used for the insider-override check: an insider's own trade files
        within 2 business days, so when one exists for a ticker Congress has
        also just traded, it's the fresher of the two signals. Never raises --
        any network/parsing failure just means "no override candidate", not a
        crash of the whole bot run."""
        try:
            filings = self._recent_form4_filings(ticker, lookback_days)
        except Exception:
            logger.warning("Could not fetch SEC EDGAR Form 4 filings for %s", ticker, exc_info=True)
            return None

        most_recent: InsiderTransaction | None = None
        for filing in filings:
            try:
                transactions = self._fetch_transactions(filing["directory_url"])
            except Exception:
                continue
            for txn in transactions:
                if txn.transaction_code not in _DIRECTIONAL_CODES or not txn.transaction_date:
                    continue
                if most_recent is None or txn.transaction_date > most_recent.transaction_date:
                    most_recent = txn
            time.sleep(0.15)  # be polite to SEC's servers between requests
        return most_recent

    def _recent_form4_filings(self, ticker: str, lookback_days: int) -> list[dict]:
        # EDGAR's "getcompany" CIK parameter accepts a ticker symbol directly.
        resp = self._session.get(
            f"{SEC_BASE_URL}/cgi-bin/browse-edgar",
            params={
                "action": "getcompany",
                "CIK": ticker,
                "type": "4",
                "owner": "include",
                "count": 40,
                "output": "atom",
            },
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        cutoff = date.today() - timedelta(days=lookback_days)

        filings = []
        for entry in root.findall("a:entry", _ATOM_NS):
            content = entry.find("a:content", _ATOM_NS)
            if content is None:
                continue
            filing_date_str = content.findtext("a:filing-date", default="", namespaces=_ATOM_NS)
            href = content.findtext("a:filing-href", default="", namespaces=_ATOM_NS)
            if not filing_date_str or not href:
                continue
            try:
                filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if filing_date < cutoff:
                continue
            filings.append({"directory_url": href.rsplit("/", 1)[0]})
        return filings

    def _fetch_transactions(self, directory_url: str) -> list[InsiderTransaction]:
        resp = self._session.get(f"{directory_url}/index.json", timeout=30)
        resp.raise_for_status()
        items = resp.json()["directory"]["item"]
        xml_name = next(
            (i["name"] for i in items if i["name"].endswith(".xml") and "index" not in i["name"].lower()),
            None,
        )
        if not xml_name:
            return []

        xml_resp = self._session.get(f"{directory_url}/{xml_name}", timeout=30)
        xml_resp.raise_for_status()
        return self._parse_form4_xml(xml_resp.content)

    def _parse_form4_xml(self, xml_bytes: bytes) -> list[InsiderTransaction]:
        root = ET.fromstring(xml_bytes)
        issuer = root.find("issuer")
        if issuer is None:
            return []
        ticker = (issuer.findtext("issuerTradingSymbol") or "").strip()
        if not ticker:
            return []

        owner = root.find("reportingOwner")
        name = "unknown"
        is_officer = is_director = False
        if owner is not None:
            name = owner.findtext("reportingOwnerId/rptOwnerName") or "unknown"
            relationship = owner.find("reportingOwnerRelationship")
            if relationship is not None:
                is_officer = _parse_bool_flag(relationship.findtext("isOfficer"))
                is_director = _parse_bool_flag(relationship.findtext("isDirector"))

        table = root.find("nonDerivativeTable")
        if table is None:
            return []

        transactions = []
        for txn in table.findall("nonDerivativeTransaction"):
            coding = txn.find("transactionCoding")
            code = (coding.findtext("transactionCode") or "") if coding is not None else ""
            transactions.append(
                InsiderTransaction(
                    ticker=ticker,
                    insider_name=name,
                    is_officer=is_officer,
                    is_director=is_director,
                    transaction_code=code,
                    transaction_date=txn.findtext("transactionDate/value") or "",
                    shares=float(txn.findtext("transactionAmounts/transactionShares/value") or 0),
                    price_per_share=float(
                        txn.findtext("transactionAmounts/transactionPricePerShare/value") or 0
                    ),
                )
            )
        return transactions
