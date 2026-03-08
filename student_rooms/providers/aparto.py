"""
providers/aparto.py — Aparto accommodation provider (apartostudent.com).

Dynamically supports all 14 cities where Aparto operates.

Scraping strategy:
  1. Discover properties for the target city by scraping apartostudent.com
  2. Establish StarRez portal session (country-specific routing)
  3. Probe a range of termIDs via direct room search URLs
  4. Filter terms by matching property names against the target city
  5. Enrich with pricing data from property pages

StarRez portal topology:
  - EU entry portal → portal.apartostudent.com/StarRezPortalXEU (country selection)
  - IE/ES/IT terms → apartostudent.starrezhousing.com/StarRezPortal (single pool)
  - UK terms → apartostudentuk.starrezhousing.com/StarRezPortal (separate pool)
  - France → no StarRez portal (discover-only)
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from student_rooms.models.config import AcademicYearConfig
from student_rooms.providers.base import BaseProvider, RoomOption

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAIN_BASE = "https://apartostudent.com"

# EU portal: entry point for country selection (shared by all countries)
PORTAL_EU_BASE = "https://portal.apartostudent.com/StarRezPortalXEU"
STARREZ_ENTRY_URL = (
    f"{PORTAL_EU_BASE}/F33813C2/65/1556/"
    "Book_a_room-Choose_Your_Country?UrlToken=8E2FC74D"
)

# StarRez portals for term probing (by region)
PORTAL_IE_BASE = "https://apartostudent.starrezhousing.com/StarRezPortal"
PORTAL_UK_BASE = "https://apartostudentuk.starrezhousing.com/StarRezPortal"

# Country → StarRez portal mapping
# IE/ES/IT all share the IE portal; UK has its own; FR has none
COUNTRY_PORTAL_MAP: Dict[str, Dict[str, Any]] = {
    "Ireland":  {"portal_base": PORTAL_IE_BASE, "country_id": "1"},
    "Spain":    {"portal_base": PORTAL_IE_BASE, "country_id": "4"},
    "Italy":    {"portal_base": PORTAL_IE_BASE, "country_id": "0"},
    "UK":       {"portal_base": PORTAL_UK_BASE, "country_id": "3"},
    "France":   {"portal_base": None,           "country_id": None},
}

# City → country mapping (all 14 cities)
CITY_COUNTRY_MAP: Dict[str, str] = {
    "Dublin":          "Ireland",
    "Barcelona":       "Spain",
    "Milan":           "Italy",
    "Florence":        "Italy",
    "Paris":           "France",
    "Aberdeen":        "UK",
    "Brighton":        "UK",
    "Bristol":         "UK",
    "Cambridge":       "UK",
    "Glasgow":         "UK",
    "Kingston":        "UK",
    "Kingston-London": "UK",
    "Lancaster":       "UK",
    "Oxford":          "UK",
    "Reading":         "UK",
}

# City → URL slug on apartostudent.com/locations/
CITY_SLUG_MAP: Dict[str, str] = {
    "Dublin":          "dublin",
    "Barcelona":       "barcelona",
    "Milan":           "milan",
    "Florence":        "florence",
    "Paris":           "paris",
    "Aberdeen":        "aberdeen",
    "Brighton":        "brighton",
    "Bristol":         "bristol",
    "Cambridge":       "cambridge",
    "Glasgow":         "glasgow",
    "Kingston":        "kingston-london",
    "Kingston-London": "kingston-london",
    "Lancaster":       "lancaster",
    "Oxford":          "oxford",
    "Reading":         "reading",
}

# Default scan range for termID probing
# IE portal: terms range from ~100 to ~1500+ (as of Feb 2026).
# For efficiency, the default range targets the recent 400 IDs where
# current-year terms cluster.  A full historical scan can be done by
# explicitly passing start_id=100.
DEFAULT_TERM_SCAN_START = 1200
DEFAULT_TERM_SCAN_END = 1600
DEFAULT_TERM_SCAN_MAX_CONSECUTIVE_MISSES = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IE,en;q=0.9",
}

# Semester 1 detection
SEMESTER1_KEYWORDS = ["semester 1", "sem 1", "semester1", "first semester"]
SEMESTER1_MAX_WEEKS = 25
FULL_YEAR_MIN_WEEKS = 35


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StarRezTerm:
    """A booking term discovered via termID probing."""
    term_id: int
    term_name: str          # e.g. "Binary Hub - 26/27 - 41 Weeks"
    property_name: str      # e.g. "Binary Hub"
    start_date: Optional[str]  # DD/MM/YYYY format
    end_date: Optional[str]
    start_iso: Optional[str]   # YYYY-MM-DD from data attributes
    end_iso: Optional[str]
    weeks: Optional[int]
    is_target_city: bool
    is_semester1: bool
    has_rooms: bool
    booking_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch(
    session: requests.Session,
    url: str,
    timeout: int = 20,
    retries: int = 3,
) -> Optional[str]:
    """Fetch URL with retries; return HTML text or None on failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                return None
            logger.warning("HTTP %s fetching %s (attempt %s/%s)", resp.status_code, url, attempt, retries)
        except requests.RequestException as exc:
            logger.warning("Request error fetching %s: %s (attempt %s/%s)", url, exc, attempt, retries)
        if attempt < retries:
            time.sleep(1.5 * attempt)
    return None


def _extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    """Extract the __NEXT_DATA__ JSON embedded by Next.js."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag and tag.string:
            return json.loads(tag.string)
    except Exception as exc:
        logger.debug("__NEXT_DATA__ parse error: %s", exc)
    return None


def _extract_rsc_json_chunks(html: str) -> List[Any]:
    """
    Extract JSON objects pushed via Next.js RSC:
      self.__next_f.push([1, '...json...'])
    """
    results = []
    pattern = re.compile(r'self\.__next_f\.push\(\[1\s*,\s*"((?:[^"\\]|\\.)*)"\]\)', re.DOTALL)
    for match in pattern.finditer(html):
        raw = match.group(1)
        try:
            unescaped = raw.encode("utf-8").decode("unicode_escape")
        except Exception:
            unescaped = raw.replace('\\"', '"').replace("\\n", "\n")
        try:
            for line in unescaped.splitlines():
                colon_idx = line.find(":")
                if colon_idx < 0:
                    continue
                json_part = line[colon_idx + 1:]
                if json_part.startswith("{") or json_part.startswith("["):
                    try:
                        results.append(json.loads(json_part))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
    return results


def _extract_prices_from_html(html: str, property_name: str) -> List[Dict[str, Any]]:
    """Parse room types and prices from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    APARTO_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]

    rooms = []
    seen_tiers: set = set()

    proximity_pattern = re.compile(
        r'(Bronze|Silver|Gold|Platinum|Studio|Deluxe)[\s\-]*(Ensuite|En-suite|Studio|Room|Suite|Apartment)?'
        r'.{0,200}?[€£]\s*(\d+(?:[.,]\d+)?)\s*(?:p/?w|/week|per week|pw)',
        re.IGNORECASE | re.DOTALL,
    )
    for m in proximity_pattern.finditer(text):
        tier = m.group(1).strip().title()
        subtype = (m.group(2) or "Ensuite").strip().title()
        label = f"{tier} {subtype}"
        if label in seen_tiers:
            continue
        seen_tiers.add(label)
        try:
            price = float(m.group(3).replace(",", "."))
        except ValueError:
            price = None
        rooms.append({
            "room_type": label,
            "price_label": f"€{price:.0f}/week" if price else "price N/A",
            "price_weekly": price,
        })

    if rooms:
        tier_order = {t: i for i, t in enumerate(APARTO_TIERS)}
        rooms.sort(key=lambda r: tier_order.get(r["room_type"].split()[0].title(), 99))
        return rooms

    # Fallback: check for monthly pricing (common for ES/IT)
    monthly_pattern = re.compile(
        r'[€£]\s*(\d+(?:[.,]\d+)?)\s*(?:per month|/month|p/?m|pcm)',
        re.IGNORECASE,
    )
    monthly_prices = monthly_pattern.findall(text)
    if monthly_prices:
        prices = sorted({float(p.replace(",", ".")) for p in monthly_prices if p})
        if prices:
            return [{
                "room_type": "Room",
                "price_label": f"from €{prices[0]:.0f}/month",
                "price_weekly": round(prices[0] / 4.33, 2),  # approximate
            }]

    # Fallback: separate tier list + price list
    tier_pattern = re.compile(
        r'\b(Bronze|Silver|Gold|Platinum|Studio|Deluxe)\b'
        r'[\s\-]*(Ensuite|En-suite|Room|Suite|Apartment)?',
        re.IGNORECASE,
    )
    found_tiers = []
    for m in tier_pattern.finditer(text):
        tier = m.group(1).strip().title()
        subtype = (m.group(2) or "Ensuite").strip().title()
        label = f"{tier} {subtype}"
        if label not in found_tiers:
            found_tiers.append(label)

    price_pattern = re.compile(r'[€£]\s*(\d+(?:[.,]\d+)?)\s*(?:p/?w|/week|per week|pw)', re.IGNORECASE)
    prices_raw = price_pattern.findall(text)
    prices = sorted({float(p.replace(",", ".")) for p in prices_raw if p})

    if not found_tiers:
        weekly = prices[0] if prices else None
        return [{
            "room_type": "Room (type TBC)",
            "price_label": f"from €{weekly:.0f}/week" if weekly else "price N/A",
            "price_weekly": weekly,
        }]

    tier_order = {t: i for i, t in enumerate(APARTO_TIERS)}
    found_tiers.sort(key=lambda l: tier_order.get(l.split()[0].title(), 99))

    for idx, tier_label in enumerate(found_tiers):
        weekly = prices[idx] if idx < len(prices) else (prices[0] if prices else None)
        rooms.append({
            "room_type": tier_label,
            "price_label": f"€{weekly:.0f}/week" if weekly else "price N/A",
            "price_weekly": weekly,
        })

    return rooms


def _extract_rooms_from_next_data(next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attempt to extract room data from __NEXT_DATA__."""
    rooms = []
    try:
        def _walk(obj: Any, depth: int = 0):
            if depth > 10:
                return
            if isinstance(obj, dict):
                name = obj.get("name") or obj.get("title") or obj.get("roomType") or ""
                price = obj.get("price") or obj.get("priceFrom") or obj.get("weeklyPrice") or 0
                if name and price and any(
                    kw in str(name).lower()
                    for kw in ("bronze", "silver", "gold", "platinum", "ensuite", "studio", "room")
                ):
                    weekly = None
                    try:
                        weekly = float(str(price).replace("€", "").replace("£", "").replace(",", "").strip())
                    except ValueError:
                        pass
                    rooms.append({
                        "room_type": str(name).strip().title(),
                        "price_label": f"€{weekly:.0f}/week" if weekly else str(price),
                        "price_weekly": weekly,
                    })
                for v in obj.values():
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)
        _walk(next_data)
    except Exception as exc:
        logger.debug("next_data room extraction error: %s", exc)
    return rooms


# ---------------------------------------------------------------------------
# Dynamic property discovery
# ---------------------------------------------------------------------------

def _discover_city_properties(
    session: requests.Session,
    city_slug: str,
) -> List[Dict[str, str]]:
    """
    Scrape apartostudent.com/locations/{city_slug} to discover properties.

    Returns a list of dicts with keys: slug, name, location, url.
    """
    url = f"{MAIN_BASE}/locations/{city_slug}"
    html = _fetch(session, url)
    if not html:
        logger.warning("Could not fetch city page: %s", url)
        return []

    soup = BeautifulSoup(html, "html.parser")
    properties: List[Dict[str, str]] = []
    seen_slugs: Set[str] = set()

    # Find all links to /locations/{city_slug}/{property_slug}
    pattern = re.compile(rf'^{re.escape(MAIN_BASE)}/locations/{re.escape(city_slug)}/([a-z0-9-]+)/?$')
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if not href.startswith("http"):
            href = MAIN_BASE + href
        m = pattern.match(href.rstrip("/"))
        if not m:
            continue
        slug = m.group(1)
        if slug in seen_slugs or slug == "short-stays":
            continue
        seen_slugs.add(slug)

        # Derive display name from slug
        name = slug.replace("-", " ").title()

        # Try to get address text near the link
        parent = link.find_parent(["div", "section", "article"])
        location = ""
        if parent:
            addr_text = parent.get_text(separator=" ").strip()
            # Look for address patterns (street names, postcodes)
            addr_match = re.search(
                r'((?:Carrer|Calle|Via|Rue|Street|St|Rd|Road|Square|Place|Point|Tce|Terrace)\s+[^,\n]{3,50}(?:,\s*[^,\n]{3,30})?)',
                addr_text,
                re.IGNORECASE,
            )
            if addr_match:
                location = addr_match.group(1).strip()

        properties.append({
            "slug": slug,
            "name": name,
            "location": location,
            "url": f"{MAIN_BASE}/locations/{city_slug}/{slug}",
        })

    if not properties:
        logger.warning("No properties found for city slug: %s", city_slug)

    return properties


def _normalise_name(name: str) -> str:
    """Normalise a property name for fuzzy matching."""
    return re.sub(r'[^a-z0-9\s]', '', name.lower()).strip()


def _build_property_aliases(properties: List[Dict[str, str]]) -> Dict[str, str]:
    """
    Build a mapping of common abbreviations/aliases → canonical property name.

    This handles StarRez term names that use abbreviations:
      "PA" → "Pallars"
      "CdM" → "Cristobal de Moura"
      "Rifredi" → "Rifredi"
    """
    aliases: Dict[str, str] = {}
    for prop in properties:
        name = prop["name"]
        norm = _normalise_name(name)
        aliases[norm] = name

        # Generate common abbreviations
        # e.g. "Cristobal De Moura" → "cdm"
        words = name.split()
        if len(words) >= 2:
            initials = "".join(w[0].lower() for w in words if w[0].isupper() or len(w) > 2)
            if len(initials) >= 2:
                aliases[initials] = name

        # Also map just the first word (e.g. "Pallars" from "Pallars Barcelona")
        if words:
            aliases[words[0].lower()] = name

    return aliases


# ---------------------------------------------------------------------------
# Term analysis
# ---------------------------------------------------------------------------

def _parse_weeks_from_name(term_name: str) -> Optional[int]:
    """Extract week count from term name like 'Binary Hub - 26/27 - 41 Weeks'."""
    m = re.search(r'(\d+)\s*[Ww]eek', term_name)
    return int(m.group(1)) if m else None


def _parse_months_from_name(term_name: str) -> Optional[int]:
    """Extract month count from term name like 'Pallars - 26/27 - 12 months'."""
    m = re.search(r'(\d+)\s*[Mm]onth', term_name)
    return int(m.group(1)) if m else None


def _extract_property_name(term_name: str) -> str:
    """Extract property name from term name like 'Binary Hub - 26/27 - 41 Weeks'.

    Handles variations:
      - "Binary Hub - 26/27 - 41 Weeks"
      - "Cristobal de Moura -26/27-Semester 1-10%"  (missing space)
      - "aparto Cristobal de Moura-September 2024"
      - "PA - 26/27 - Generic Group"
    """
    # Standard format: "Name - YY/YY - ..."
    if " - " in term_name:
        return term_name.split(" - ")[0].strip()
    # Handle "Name -YY/YY-..." (no space before dash + year pattern)
    m = re.match(r'^(.+?)\s*-\s*\d{2}/\d{2}', term_name)
    if m:
        return m.group(1).strip()
    # Handle "aparto Name-Something"
    if term_name.lower().startswith("aparto "):
        remainder = term_name[7:]
        if "-" in remainder:
            return remainder.split("-")[0].strip()
        return remainder.strip()
    return term_name


def _is_target_city_term(
    term_name: str,
    target_property_names: Set[str],
    property_aliases: Dict[str, str],
) -> bool:
    """
    Check if a term belongs to a property in the target city.

    Uses fuzzy matching against dynamically discovered property names
    and their aliases/abbreviations.
    """
    prop_name_raw = _extract_property_name(term_name)
    prop_name_norm = _normalise_name(prop_name_raw)

    # Direct match against known property names
    for known_name in target_property_names:
        known_norm = _normalise_name(known_name)
        if known_norm in prop_name_norm or prop_name_norm in known_norm:
            return True

    # Check aliases (handles abbreviations like PA, CdM)
    for alias, canonical in property_aliases.items():
        if alias == prop_name_norm or prop_name_norm.startswith(alias + " "):
            return True
        # Also check if the alias appears at the start of the term name
        term_start = _normalise_name(term_name.split("-")[0].strip() if "-" in term_name else term_name)
        if alias == term_start:
            return True

    return False


def _is_semester1_term(
    term_name: str,
    start_date: Optional[str],
    end_date: Optional[str],
    weeks: Optional[int],
) -> bool:
    """
    Detect if a term is a Semester 1 option.

    Checks:
    1. Name contains semester 1 keywords
    2. Duration is <= 25 weeks (not full year)
    3. Start date is August/September/October
    4. End date is December/January/February
    """
    name_lower = term_name.lower()

    # Direct keyword match
    if any(kw in name_lower for kw in SEMESTER1_KEYWORDS):
        return True

    # Duration-based detection
    if weeks is not None and weeks <= SEMESTER1_MAX_WEEKS:
        if start_date and end_date:
            try:
                s = datetime.strptime(start_date, "%d/%m/%Y")
                e = datetime.strptime(end_date, "%d/%m/%Y")
                if s.month in (8, 9, 10) and e.month in (12, 1, 2):
                    return True
            except ValueError:
                pass

    # ISO date format fallback
    if start_date and end_date and "-" in start_date:
        try:
            s = datetime.strptime(start_date, "%Y-%m-%d")
            e = datetime.strptime(end_date, "%Y-%m-%d")
            duration_weeks = (e - s).days / 7
            if (duration_weeks <= SEMESTER1_MAX_WEEKS and
                s.month in (8, 9, 10) and
                e.month in (12, 1, 2)):
                return True
        except ValueError:
            pass

    return False


# ---------------------------------------------------------------------------
# StarRez portal session & term probing
# ---------------------------------------------------------------------------

class StarRezScraper:
    """
    Navigate the StarRez Aparto portal and probe termIDs.

    Supports all countries via the appropriate regional portal.
    IE/ES/IT share a single portal; UK has its own.
    """

    def __init__(
        self,
        session: requests.Session,
        portal_base: str,
        country_id: Optional[str] = None,
    ):
        self.session = session
        self.portal_base = portal_base
        self.country_id = country_id
        self._session_established = False

    def _establish_session(self) -> bool:
        """Navigate EU portal → target country to establish session cookies."""
        if self._session_established:
            return True

        try:
            r1 = self.session.get(STARREZ_ENTRY_URL, headers=HEADERS, timeout=20)
            if r1.status_code != 200:
                logger.warning("StarRez entry page HTTP %d", r1.status_code)
                return False

            soup = BeautifulSoup(r1.text, "html.parser")
            form = soup.find("form")
            if not form:
                logger.warning("No form on StarRez entry page")
                return False

            # Select the target country
            country_value = self.country_id or "1"  # Default to Ireland
            fields: Dict[str, str] = {}
            for inp in soup.find_all("input"):
                name = inp.get("name")
                if name:
                    fields[name] = inp.get("value", "")

            fields["CheckOrderList"] = country_value

            action = form.get("action", "")
            post_url = f"https://portal.apartostudent.com/StarRezPortalXEU{action}"

            time.sleep(0.3)
            r2 = self.session.post(post_url, data=fields, headers=HEADERS, timeout=20, allow_redirects=False)
            redirect_path = r2.text.strip().strip('"')
            if not redirect_path or not redirect_path.startswith("/"):
                logger.warning("Unexpected redirect response: %s", r2.text[:100])
                return False

            time.sleep(0.3)
            r3 = self.session.get(
                f"https://portal.apartostudent.com{redirect_path}",
                headers=HEADERS,
                timeout=20,
                allow_redirects=True,
            )
            if r3.status_code != 200:
                logger.warning("Residence page HTTP %d", r3.status_code)
                return False

            self._session_established = True
            logger.info("StarRez session established for country %s: %s", country_value, r3.url)
            return True

        except requests.RequestException as exc:
            logger.warning("StarRez session error: %s", exc)
            return False

    def probe_term(self, term_id: int) -> Optional[StarRezTerm]:
        """
        Probe a single termID by accessing its room search redirect URL.
        Returns StarRezTerm if valid, None if invalid/error.

        Note: target_property_names and property_aliases are set after
        probing via _annotate_term().
        """
        url = (
            f"{self.portal_base}/General/RoomSearch/RoomSearch/RedirectToMainFilter"
            f"?roomSelectionModelID=361&filterID=1&option=RoomLocationArea&termID={term_id}"
        )
        try:
            r = self.session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                return None
            if "Choose your room" not in r.text:
                return None
        except requests.RequestException:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # Extract term name + dates from info text
        term_info_match = re.search(
            r"You have selected '([^']+)' booking term.*?"
            r"begins on (\d{2}/\d{2}/\d{4}).*?"
            r"ends on (\d{2}/\d{2}/\d{4})",
            r.text,
            re.DOTALL,
        )

        # Get ISO dates from data attributes
        page_container = soup.find(attrs={"data-termid": True})
        start_iso = page_container.get("data-datestart", "")[:10] if page_container else None
        end_iso = page_container.get("data-dateend", "")[:10] if page_container else None

        term_name = term_info_match.group(1) if term_info_match else f"Term {term_id}"
        start_date = term_info_match.group(2) if term_info_match else None
        end_date = term_info_match.group(3) if term_info_match else None

        property_name = _extract_property_name(term_name)
        weeks = _parse_weeks_from_name(term_name)

        # For Semester 1 detection, use DD/MM/YYYY if available, else ISO
        is_sem1 = _is_semester1_term(
            term_name,
            start_date or start_iso,
            end_date or end_iso,
            weeks,
        )

        # Check for actual room listings
        has_rooms = (
            "room-result" in r.text.lower()
            or "€" in soup.get_text()
            or "£" in soup.get_text()
            or bool(soup.find(attrs={"data-roombaseid": True}))
        )

        return StarRezTerm(
            term_id=term_id,
            term_name=term_name,
            property_name=property_name,
            start_date=start_date,
            end_date=end_date,
            start_iso=start_iso,
            end_iso=end_iso,
            weeks=weeks,
            is_target_city=False,  # Will be set by caller
            is_semester1=is_sem1,
            has_rooms=has_rooms,
            booking_url=r.url,
        )

    def scan_term_range(
        self,
        target_property_names: Set[str],
        property_aliases: Dict[str, str],
        start_id: int = DEFAULT_TERM_SCAN_START,
        end_id: int = DEFAULT_TERM_SCAN_END,
        target_city_only: bool = True,
        delay: float = 0.05,
        total_timeout: float = 90.0,
    ) -> List[StarRezTerm]:
        """
        Scan a range of termIDs and return valid terms for the target city.

        Uses a smart scanning strategy:
        1. Start from start_id and scan upward
        2. Stop after max_consecutive_misses misses past the last hit
        3. Enforce a total timeout to prevent long scans
        """
        if not self._establish_session():
            logger.error("Failed to establish StarRez session")
            return []

        terms: List[StarRezTerm] = []
        consecutive_misses = 0
        last_hit_id = start_id
        processed = 0
        timed_out = False
        start_time = time.monotonic()
        deadline = start_time + total_timeout

        max_workers = 8
        max_in_flight = max_workers * 4
        next_id = start_id
        next_expected = start_id
        pending: Dict[concurrent.futures.Future, int] = {}
        ready: Dict[int, Optional[StarRezTerm]] = {}
        stop_early = False

        def _submit_next(executor: concurrent.futures.ThreadPoolExecutor) -> bool:
            nonlocal next_id
            if next_id > end_id:
                return False
            if time.monotonic() >= deadline:
                return False
            if delay > 0:
                time.sleep(delay)
            future = executor.submit(self.probe_term, next_id)
            pending[future] = next_id
            next_id += 1
            return True

        def _process_term(tid: int, term: Optional[StarRezTerm]) -> None:
            nonlocal consecutive_misses, last_hit_id, stop_early, processed
            processed += 1
            if term:
                consecutive_misses = 0
                last_hit_id = tid

                # Check if this term belongs to the target city
                is_target = _is_target_city_term(
                    term.term_name,
                    target_property_names,
                    property_aliases,
                )
                term.is_target_city = is_target

                if target_city_only and not is_target:
                    logger.debug(
                        "Term %d: %s (not target city, skipping)",
                        tid, term.term_name,
                    )
                else:
                    terms.append(term)
                    logger.debug(
                        "Term %d: %s (%s → %s) target=%s sem1=%s",
                        tid, term.term_name, term.start_date, term.end_date,
                        term.is_target_city, term.is_semester1,
                    )
            else:
                consecutive_misses += 1
                if (consecutive_misses > DEFAULT_TERM_SCAN_MAX_CONSECUTIVE_MISSES
                        and tid > last_hit_id + DEFAULT_TERM_SCAN_MAX_CONSECUTIVE_MISSES):
                    logger.debug(
                        "Stopping scan at termID %d (%d consecutive misses past last hit %d)",
                        tid, consecutive_misses, last_hit_id,
                    )
                    stop_early = True

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                while len(pending) < max_in_flight and next_id <= end_id:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    if not _submit_next(executor):
                        break

                if time.monotonic() >= deadline:
                    timed_out = True
                    break

                if next_expected in ready:
                    term = ready.pop(next_expected)
                    _process_term(next_expected, term)
                    next_expected += 1
                    if stop_early:
                        break
                    continue

                if not pending:
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break

                done, _ = concurrent.futures.wait(
                    pending.keys(),
                    timeout=min(0.5, remaining),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                if not done:
                    continue

                for future in done:
                    tid = pending.pop(future)
                    try:
                        ready[tid] = future.result()
                    except Exception:
                        ready[tid] = None

                if stop_early:
                    break

            if timed_out or stop_early:
                for future in pending:
                    future.cancel()

        scanned = processed
        if timed_out:
            logger.warning(
                "StarRez scan timed out after %.1fs: %d/%d termIDs checked, %d target city terms found",
                total_timeout,
                scanned,
                end_id - start_id + 1,
                len(terms),
            )
            return terms

        logger.info(
            "StarRez scan complete: %d/%d termIDs checked, %d target city terms found",
            scanned,
            end_id - start_id + 1,
            len(terms),
        )
        return terms


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

class ApartoProvider(BaseProvider):
    """
    Aparto provider: dynamic property discovery + StarRez termID probing.

    Supports all 14 Aparto cities. Properties are discovered dynamically
    from the main site; booking terms are probed via StarRez portals.
    """

    def __init__(
        self,
        city: str = "Dublin",
        country: Optional[str] = None,
    ):
        self._session = requests.Session()
        self._city = city.strip().title()
        self._country = country or self._resolve_country(self._city)
        self._city_slug = self._resolve_city_slug(self._city)
        self._portal_config = COUNTRY_PORTAL_MAP.get(self._country, {})
        self._discovered_properties: Optional[List[Dict[str, str]]] = None
        self._property_names: Optional[Set[str]] = None
        self._property_aliases: Optional[Dict[str, str]] = None

    @staticmethod
    def _resolve_country(city: str) -> str:
        """Resolve country from city name."""
        # Try exact match first
        country = CITY_COUNTRY_MAP.get(city)
        if country:
            return country
        # Try case-insensitive match
        city_lower = city.lower()
        for k, v in CITY_COUNTRY_MAP.items():
            if k.lower() == city_lower:
                return v
        logger.warning("Unknown city '%s', defaulting to Ireland", city)
        return "Ireland"

    @staticmethod
    def _resolve_city_slug(city: str) -> str:
        """Resolve URL slug from city name."""
        slug = CITY_SLUG_MAP.get(city)
        if slug:
            return slug
        city_lower = city.lower()
        for k, v in CITY_SLUG_MAP.items():
            if k.lower() == city_lower:
                return v
        return city.lower().replace(" ", "-")

    def _ensure_properties_discovered(self) -> None:
        """Lazy-discover properties for the target city."""
        if self._discovered_properties is not None:
            return

        self._discovered_properties = _discover_city_properties(
            self._session, self._city_slug,
        )
        self._property_names = {p["name"] for p in self._discovered_properties}
        self._property_aliases = _build_property_aliases(self._discovered_properties)

        logger.info(
            "Aparto: discovered %d properties for %s: %s",
            len(self._discovered_properties),
            self._city,
            [p["name"] for p in self._discovered_properties],
        )

    @property
    def name(self) -> str:
        return "aparto"

    def discover_properties(self) -> List[Dict[str, Any]]:
        """Dynamically discover properties for the configured city."""
        self._ensure_properties_discovered()
        props = []
        for prop in self._discovered_properties:
            props.append({
                "slug": prop["slug"],
                "name": prop["name"],
                "location": prop.get("location", ""),
                "url": prop["url"],
                "provider": "aparto",
                "city": self._city,
                "country": self._country,
            })
        return props

    def _scrape_property(self, prop: Dict[str, str]) -> List[Dict[str, Any]]:
        """Scrape a single property page for room types + prices."""
        url = prop.get("url") or f"{MAIN_BASE}/locations/{self._city_slug}/{prop['slug']}"
        html = _fetch(self._session, url)
        if not html:
            logger.warning("Aparto: could not fetch %s", url)
            return []

        rooms: List[Dict[str, Any]] = []

        next_data = _extract_next_data(html)
        if next_data:
            next_rooms = _extract_rooms_from_next_data(next_data)
            if next_rooms:
                rooms = next_rooms

        if not rooms:
            rooms = _extract_prices_from_html(html, prop["name"])

        for room in rooms:
            room.update({
                "property_name": prop["name"],
                "property_slug": prop["slug"],
                "location": prop.get("location", ""),
                "page_url": url,
            })

        return rooms

    def scan(
        self,
        academic_year: str = "2026-27",
        semester: int = 1,
        apply_semester_filter: bool = True,
        academic_config: Optional[AcademicYearConfig] = None,
    ) -> List[RoomOption]:
        """
        Full scan: probe StarRez termIDs + scrape main site for prices.

        Strategy:
        1. Discover properties for the target city
        2. Scan termIDs to find booking terms matching those properties
        3. Filter for Semester 1 terms (or return all if filter is off)
        4. Enrich with pricing data from the main site
        """
        self._ensure_properties_discovered()
        results: List[RoomOption] = []

        portal_base = self._portal_config.get("portal_base")
        if not portal_base:
            logger.warning(
                "Aparto: no StarRez portal for %s (%s). Scan not available.",
                self._city, self._country,
            )
            return results

        # Step 1: Probe StarRez termIDs
        scraper = StarRezScraper(
            self._session,
            portal_base=portal_base,
            country_id=self._portal_config.get("country_id"),
        )
        all_terms = scraper.scan_term_range(
            target_property_names=self._property_names,
            property_aliases=self._property_aliases,
            target_city_only=True,
        )
        logger.info("Aparto: found %d terms for %s", len(all_terms), self._city)

        # Filter by academic year (26/27)
        year_short = academic_year.replace("-", "/")[-5:]  # "2026-27" → "26/27"
        year_terms = [
            t for t in all_terms
            if year_short in t.term_name or (
                t.start_iso and t.start_iso.startswith(academic_year[:4]) and
                t.end_iso and (
                    t.end_iso.startswith(f"20{academic_year[-2:]}") or
                    t.end_iso.startswith(academic_year[:4])
                )
            )
        ]

        # Apply semester filter
        if apply_semester_filter and semester == 1:
            target_terms = [t for t in year_terms if t.is_semester1]
        else:
            target_terms = year_terms

        if not target_terms:
            sem1_count = sum(1 for t in year_terms if t.is_semester1)
            logger.info(
                "Aparto: %d year terms, %d Semester 1 terms (filter=%s)",
                len(year_terms), sem1_count, apply_semester_filter,
            )
            if not apply_semester_filter:
                target_terms = year_terms

        if not target_terms:
            return results

        # Step 2: Get pricing data from main site
        property_rooms: Dict[str, List[Dict]] = {}
        prop_lookup = {_normalise_name(p["name"]): p for p in self._discovered_properties}
        for prop in self._discovered_properties:
            time.sleep(0.5)
            rooms = self._scrape_property(prop)
            if rooms:
                property_rooms[prop["slug"]] = rooms

        # Step 3: Build RoomOptions
        for term in target_terms:
            prop_info = None
            term_prop_norm = _normalise_name(term.property_name)

            # Try direct match
            for norm_name, info in prop_lookup.items():
                if norm_name in term_prop_norm or term_prop_norm in norm_name:
                    prop_info = info
                    break

            # Try alias match
            if not prop_info and self._property_aliases:
                canonical = self._property_aliases.get(term_prop_norm)
                if canonical:
                    for norm_name, info in prop_lookup.items():
                        if _normalise_name(canonical) == norm_name:
                            prop_info = info
                            break

            slug = prop_info["slug"] if prop_info else term.property_name.lower().replace(" ", "-")
            location = prop_info.get("location", "") if prop_info else ""

            rooms = property_rooms.get(slug, [])
            if not rooms:
                rooms = [{"room_type": "Room (type TBC)", "price_weekly": None, "price_label": "price TBC"}]

            for room in rooms:
                results.append(RoomOption(
                    provider="aparto",
                    property_name=term.property_name,
                    property_slug=slug,
                    room_type=room.get("room_type", "Room"),
                    price_weekly=room.get("price_weekly"),
                    price_label=room.get("price_label", ""),
                    available=True,
                    booking_url=term.booking_url,
                    start_date=term.start_iso,
                    end_date=term.end_iso,
                    academic_year=academic_year,
                    option_name=term.term_name,
                    location=location,
                    raw={
                        "term_id": term.term_id,
                        "weeks": term.weeks,
                        "is_semester1": term.is_semester1,
                        "start_date_dd": term.start_date,
                        "end_date_dd": term.end_date,
                        "city": self._city,
                        "country": self._country,
                    },
                ))

        return results

    def probe_booking(self, option: RoomOption) -> Dict[str, Any]:
        """Deep-probe for a specific option."""
        self._ensure_properties_discovered()

        portal_base = self._portal_config.get("portal_base")
        if not portal_base:
            return {
                "match": {"property": option.property_name, "room": option.room_type},
                "error": f"No StarRez portal for {self._city} ({self._country})",
            }

        scraper = StarRezScraper(
            self._session,
            portal_base=portal_base,
            country_id=self._portal_config.get("country_id"),
        )
        all_terms = scraper.scan_term_range(
            target_property_names=self._property_names,
            property_aliases=self._property_aliases,
            target_city_only=True,
        )

        term_id = option.raw.get("term_id")
        matching_term = None
        if term_id:
            matching_term = next((t for t in all_terms if t.term_id == term_id), None)

        year_short = (option.academic_year or "2026-27").replace("-", "/")[-5:]
        semester1_terms = [
            t for t in all_terms
            if t.is_semester1 and year_short in t.term_name
        ]

        return {
            "match": {
                "property": option.property_name,
                "room": option.room_type,
                "academicYear": option.academic_year,
                "termName": matching_term.term_name if matching_term else "N/A",
                "hasSemester1": bool(semester1_terms),
                "city": self._city,
                "country": self._country,
            },
            "portalState": {
                "termCount": len([t for t in all_terms if year_short in t.term_name]),
                "semester1Count": len(semester1_terms),
                "allTerms": [
                    {
                        "name": t.term_name,
                        "termId": t.term_id,
                        "startDate": t.start_date,
                        "endDate": t.end_date,
                        "weeks": t.weeks,
                        "isSemester1": t.is_semester1,
                    }
                    for t in all_terms if year_short in t.term_name
                ],
            },
            "links": {
                "bookingPortal": STARREZ_ENTRY_URL,
                "propertyPage": f"{MAIN_BASE}/locations/{self._city_slug}/{option.property_slug}",
                "termLink": matching_term.booking_url if matching_term else None,
            },
            "raw": option.raw,
        }
