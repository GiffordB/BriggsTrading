"""Free, public SEC EDGAR data -- no API key or subscription needed.

This is now the bot's primary trade signal: it scans SEC EDGAR's daily
filing index for every Form 4 filed market-wide (any company, not a
pre-selected watchlist), and surfaces each genuine open-market insider buy
or sell as a mirror-able filing. A Form 4 is due within 2 business days of
the trade, far faster than the 30-45 day lag a congressional disclosure has.

SEC asks that automated requests set a descriptive User-Agent identifying
the requester (https://www.sec.gov/os/webmaster-faq#developers) -- a bare
UA with no contact info gets blocked outright ("Undeclared Automated Tool").
Set SEC_EDGAR_USER_AGENT to something identifying this project and a real
contact if you have one. This client also pauses briefly between requests
to stay well under SEC's published 10 req/sec fair-access limit.

Scale note: a single weekday's Form 4 filings number in the low thousands
market-wide, most of which are option exercises, grants, or tax withholding
rather than genuine buys/sells -- there's no way to know which, without
fetching and parsing each one's XML. A full day's scan is a few thousand
HTTP requests and can take several minutes; see MAX_INSIDER_FILINGS_PER_RUN
in config.py for the safety cap.
"""

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

SEC_BASE_URL = "https://www.sec.gov"

# The two transaction codes that represent a genuine discretionary
# open-market trade. Excludes grants/awards (A), option exercises (M),
# gifts (G), tax withholding (F), and other non-discretionary types that
# don't reflect an insider choosing to trade in the open market.
_PURCHASE_CODE = "P"
_SALE_CODE = "S"
_DIRECTIONAL_CODES = {_PURCHASE_CODE, _SALE_CODE}

# Only the base form, not amendments -- a "4/A" corrects a recently filed
# "4" (e.g. a typo'd share count), so counting both would double up on what
# is, from a mirroring standpoint, the same underlying trade.
_FORM_4_TYPE = "4"


def _parse_bool_flag(value: str | None) -> bool:
    """Handles both '1'/'0' and 'true'/'false' -- different filers' Form 4
    XML use different conventions for the same boolean fields."""
    return (value or "").strip().lower() in ("1", "true")


@dataclass(frozen=True)
class InsiderFiling:
    """One genuine open-market insider transaction, surfaced from a Form 4.
    This is the bot's equivalent of what a congressional "disclosure" used
    to be -- the unit evaluate_insider_filings() in strategy.py decides on."""

    accession: str
    ticker: str
    insider_name: str
    is_officer: bool
    is_director: bool
    transaction_code: str  # 'P' or 'S'
    transaction_date: str
    filed_date: str
    shares: float
    price_per_share: float

    @property
    def transaction_type(self) -> str:
        return "Purchase" if self.transaction_code == _PURCHASE_CODE else "Sale"

    @property
    def notional(self) -> float:
        return self.shares * self.price_per_share

    @property
    def dedupe_key(self) -> str:
        # Unique per actual transaction, not just per filing: a single Form 4
        # can report several transactions (e.g. a sale plus a tax-withholding
        # line), and this is what feeds has_seen()/mark_seen() in main.py.
        return f"{self.accession}|{self.transaction_code}|{self.transaction_date}|{self.shares}"


class SECEdgarClient:
    def __init__(self, user_agent: str, request_delay: float = 0.15):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._request_delay = request_delay

    def scan_recent_form4_filings(
        self, lookback_days: int, max_filings: int | None = None
    ) -> list[InsiderFiling]:
        """Scans the last `lookback_days` calendar days of SEC's daily filing
        index for every Form 4, market-wide, and returns the genuine
        open-market buy/sell transactions found in them. Never raises --
        a day's index failing to fetch (weekend, holiday, future date, or a
        transient SEC error) just means that day contributes nothing, not a
        crash of the whole bot run.

        `max_filings` caps how many *unique filings* get fetched and parsed
        (the expensive part -- an index.json + an XML fetch each), as a
        safety valve against a backlog (e.g. a missed run) turning into an
        hours-long scan. Filings beyond the cap are simply not seen this
        run; LOOKBACK_DAYS naturally covers them on the next one.
        """
        accessions: dict[str, dict] = {}
        for days_ago in range(lookback_days):
            day = date.today() - timedelta(days=days_ago)
            try:
                for entry in self._form4_index_entries(day):
                    # A filing with multiple participants (issuer + one or
                    # more reporting owners) is listed once per participant
                    # CIK in the daily index, all under the same accession --
                    # keep only the first one seen.
                    accessions.setdefault(entry["accession"], entry)
            except Exception:
                logger.warning("Could not fetch SEC daily index for %s", day.isoformat(), exc_info=True)

        candidates = list(accessions.values())
        if max_filings is not None and len(candidates) > max_filings:
            logger.warning(
                "%d candidate Form 4 filings found, capping to MAX_INSIDER_FILINGS_PER_RUN=%d",
                len(candidates), max_filings,
            )
            candidates = candidates[:max_filings]

        filings: list[InsiderFiling] = []
        for candidate in candidates:
            try:
                filings.extend(self._fetch_directional_transactions(candidate))
            except Exception:
                logger.debug("Could not fetch/parse filing %s", candidate["accession"], exc_info=True)
            time.sleep(self._request_delay)  # be polite to SEC's servers between requests
        return filings

    def _form4_index_entries(self, day: date) -> list[dict]:
        """One day's worth of Form 4 index rows: {accession, cik, filed_date}.
        Raises on a genuine fetch error; a missing day's file (weekend,
        holiday, or a future date) is treated as "no filings that day", not
        an error -- SEC serves that as 403, not 404, for this path."""
        quarter = (day.month - 1) // 3 + 1
        url = f"{SEC_BASE_URL}/Archives/edgar/daily-index/{day.year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
        resp = self._session.get(url, timeout=30)
        if resp.status_code in (403, 404):
            return []
        resp.raise_for_status()

        entries = []
        for line in resp.text.splitlines():
            parts = line.split()
            if not parts or parts[0] != _FORM_4_TYPE:
                continue
            # Columns are fixed-width (Form Type, Company Name, CIK, Date
            # Filed, File Name), but the company name's spaces make a plain
            # split() ambiguous -- the last three tokens are reliably CIK,
            # Date Filed, and File Name (none of which contain spaces).
            if len(parts) < 4:
                continue
            file_name = parts[-1]
            cik = parts[-3]
            filed_date = parts[-2]
            # File Name looks like "edgar/data/{cik}/{accession-with-dashes}.txt"
            accession = file_name.rsplit("/", 1)[-1].removesuffix(".txt")
            if not accession or not cik.isdigit():
                continue
            entries.append({"accession": accession, "cik": cik, "filed_date": filed_date})
        return entries

    def _fetch_directional_transactions(self, candidate: dict) -> list[InsiderFiling]:
        accession_nodash = candidate["accession"].replace("-", "")
        directory_url = f"{SEC_BASE_URL}/Archives/edgar/data/{candidate['cik']}/{accession_nodash}"

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

        filed_date = candidate["filed_date"]
        if len(filed_date) == 8:  # "YYYYMMDD" from the daily index -> "YYYY-MM-DD"
            filed_date = f"{filed_date[:4]}-{filed_date[4:6]}-{filed_date[6:]}"

        return self._parse_form4_xml(xml_resp.content, candidate["accession"], filed_date)

    def _parse_form4_xml(
        self, xml_bytes: bytes, accession: str, filed_date: str
    ) -> list[InsiderFiling]:
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

        filings = []
        for txn in table.findall("nonDerivativeTransaction"):
            coding = txn.find("transactionCoding")
            code = (coding.findtext("transactionCode") or "") if coding is not None else ""
            if code not in _DIRECTIONAL_CODES:
                continue
            transaction_date = txn.findtext("transactionDate/value") or ""
            shares = float(txn.findtext("transactionAmounts/transactionShares/value") or 0)
            price = float(txn.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
            if shares <= 0 or price <= 0:
                continue
            filings.append(
                InsiderFiling(
                    accession=accession,
                    ticker=ticker,
                    insider_name=name,
                    is_officer=is_officer,
                    is_director=is_director,
                    transaction_code=code,
                    transaction_date=transaction_date,
                    filed_date=filed_date,
                    shares=shares,
                    price_per_share=price,
                )
            )
        return filings
