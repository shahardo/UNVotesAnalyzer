"""Fetches Israel-related UN voting-data resolutions from digitallibrary.un.org.

Two things about this site were verified live (not assumed) before writing
this module, because the site blocks read-only probing in ways that made
assuming its behavior unsafe:

1. digitallibrary.un.org sits behind an AWS WAF bot-detection layer that
   outright 403s a vanilla headless-Chromium Playwright session (it
   fingerprints automation, not just "is this a browser"). A realistic
   user agent, a real viewport/locale, `--disable-blink-features=
   AutomationControlled`, and hiding `navigator.webdriver` are all required
   for it to instead show a solvable JS challenge, which then reloads into
   real content after a few seconds. Once solved in a browser context, that
   context's `request` API (which shares cookies) can call the search API
   directly without re-solving per request.
2. `of=recjson` (the documented JSON output format) turns out to return only
   a sparse, field-abstracted default view for "Voting Data" records (no
   title/vote breakdown at all) -- confirmed by fetching a real record.
   `of=xm` (MARCXML) returns the full record. The per-country vote breakdown
   lives in repeated MARC field 967 (`$c`=ISO3 code, `$e`=country name,
   `$d`=vote code "Y"/"N", with `$d` entirely ABSENT meaning abstain -- this
   was confirmed by cross-checking a record's vote counts against its own
   967 fields). The resolution symbol is in 791$a, the vote date in 269$a,
   and a topic/agenda label in 991$c (there is no MARC 650 subject heading
   on these particular records -- 991$c is the most descriptive text
   available directly on the vote record itself).
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import UndlConfig
from src.models import CountryVote, Resolution, Vote

logger = logging.getLogger(__name__)

_MARC_NS = {"m": "http://www.loc.gov/MARC21/slim"}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VOTE_CODE_MAP = {
    "Y": Vote.YES,
    "N": Vote.NO,
    "A": Vote.ABSTAIN,
}


def _sanitize_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def _datafield_subfield(datafield: ET.Element, code: str) -> str | None:
    sf = datafield.find(f"m:subfield[@code='{code}']", _MARC_NS)
    return sf.text if sf is not None else None


def _parse_votes(record: ET.Element) -> list[CountryVote]:
    votes: list[CountryVote] = []
    for df in record.findall("m:datafield[@tag='967']", _MARC_NS):
        country = _datafield_subfield(df, "e")
        code = _datafield_subfield(df, "d")
        if not country:
            continue
        vote = _VOTE_CODE_MAP.get((code or "").strip().upper(), Vote.ABSTAIN)
        votes.append(CountryVote(country=country, vote=vote))
    if not votes:
        logger.warning("no per-country votes (MARC 967) found on record")
    return votes


def _parse_record(record: ET.Element) -> Resolution | None:
    symbol = None
    title_parts: list[str] = []
    subjects: list[str] = []
    date = None

    for df in record.findall("m:datafield", _MARC_NS):
        tag = df.get("tag")
        if tag == "245":
            title_parts = [sf.text for sf in df.findall("m:subfield", _MARC_NS) if sf.text]
        elif tag == "791":
            # A record can carry more than one 791 field: the resolution's
            # own symbol in $a, plus a $z cross-reference to a related/
            # superseded resolution with no $a of its own. Only $a-bearing
            # fields count -- an empty later 791 must not clobber a symbol
            # already found.
            field_symbol = _datafield_subfield(df, "a")
            if field_symbol:
                symbol = field_symbol
        elif tag == "991":
            topic = _datafield_subfield(df, "c")
            if topic:
                subjects.append(topic)
        elif tag == "269":
            date = _datafield_subfield(df, "a")

    if not symbol:
        logger.warning("skipping record with no resolution symbol (MARC 791$a)")
        return None

    return Resolution(
        symbol=symbol,
        title=" ".join(title_parts) or symbol,
        subjects=subjects,
        date=date,
        summary="",
        votes=_parse_votes(record),
    )


def _build_query(config: UndlConfig) -> str:
    # Multi-word keywords must be quoted or Invenio's query parser will AND
    # the individual words together instead of treating them as one phrase.
    terms = " OR ".join(f'"{kw}"' for kw in config.subject_keywords)
    query = f"({terms})"
    if config.date_from and config.date_to:
        query += f" AND 269__a:{config.date_from}->{config.date_to}"
    return query


def _new_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True, args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        user_agent=_USER_AGENT, viewport={"width": 1280, "height": 800}, locale="en-US"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


def fetch_israel_resolutions(
    config: UndlConfig, raw_dir: Path, limit: int | None = None
) -> list[Resolution]:
    """Search+fetch Israel-related voting-data resolutions, caching each
    parsed resolution as JSON under raw_dir. Records already cached on disk
    are not re-fetched."""
    resolutions: list[Resolution] = []

    with sync_playwright() as playwright:
        browser, context = _new_browser_context(playwright)
        page = context.new_page()

        # Solve the WAF JS challenge once; subsequent context.request calls
        # reuse the resulting cookie. The challenge reloads the page after a
        # few seconds once solved client-side, so give it time.
        page.goto(f"{config.base_url}/search?ln=en", wait_until="load", timeout=60_000)
        page.wait_for_timeout(4000)

        query = _build_query(config)
        offset = 1
        fetched = 0
        while limit is None or fetched < limit:
            page_size = config.page_size if limit is None else min(config.page_size, limit - fetched)
            url = (
                f"{config.base_url}/search"
                f"?p={query}&cc={config.collection}&of=xm"
                f"&rg={page_size}&jrec={offset}&ln=en"
            )
            response = context.request.get(url)
            if not response.ok:
                logger.error("UNDL request failed (%s): %s", response.status, url)
                break

            try:
                root = ET.fromstring(response.text())
            except ET.ParseError:
                logger.error("UNDL response was not valid MARCXML for url: %s", url)
                break

            records = root.findall("m:record", _MARC_NS)
            if not records:
                break

            for raw_record in records:
                resolution = _parse_record(raw_record)
                if resolution is None:
                    continue
                cache_path = raw_dir / f"{_sanitize_symbol(resolution.symbol)}.json"
                if not cache_path.exists():
                    cache_path.write_text(
                        json.dumps(_resolution_to_dict(resolution), indent=2), encoding="utf-8"
                    )
                resolutions.append(resolution)
                fetched += 1
                if limit is not None and fetched >= limit:
                    break

            offset += page_size
            time.sleep(config.request_delay_seconds)

        browser.close()

    return resolutions


def _resolution_to_dict(resolution: Resolution) -> dict:
    return {
        "symbol": resolution.symbol,
        "title": resolution.title,
        "subjects": resolution.subjects,
        "date": resolution.date,
        "summary": resolution.summary,
        "votes": [{"country": v.country, "vote": v.vote.value} for v in resolution.votes],
    }


def load_cached_resolutions(raw_dir: Path) -> list[Resolution]:
    resolutions = []
    for path in sorted(raw_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        resolutions.append(
            Resolution(
                symbol=data["symbol"],
                title=data["title"],
                subjects=data["subjects"],
                date=data["date"],
                summary=data["summary"],
                votes=[
                    CountryVote(country=v["country"], vote=Vote(v["vote"]))
                    for v in data["votes"]
                ],
            )
        )
    return resolutions
