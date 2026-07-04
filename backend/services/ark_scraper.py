"""ARK Group Real Estate scraper with in-memory cache.

Discovers project pages dynamically from the ARK landing page so that
newly published projects appear automatically without code changes.

Usage
-----
    from services.ark_scraper import get_ark_projects, get_ark_projects_for_city

    all_projects    = get_ark_projects()          # list[dict]
    hyd_projects    = get_ark_projects_for_city("Hyderabad")
    forced_refresh  = get_ark_projects(force=True)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

from data.ark_geocodes import geocode_location

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
ARK_BASE_URL     = "https://arkgroup.in"
ARK_LANDING_PAGE = "https://arkgroup.in/divisions/real-estate/"
CACHE_TTL_SECONDS = 6 * 60 * 60   # 6 hours
REQUEST_TIMEOUT   = 20             # seconds per HTTP request

# URL path segments that indicate a real-estate project page
PROJECT_PATH_PREFIXES = ("/projects/", "/exclusive/")

# ── Thread-safe cache ────────────────────────────────────────────────────────
_cache_lock      = threading.Lock()
_cached_projects: list[dict] | None = None
_cache_timestamp: float = 0.0


# ── Location normalisation helpers ───────────────────────────────────────────
_CITY_ALIASES: dict[str, str] = {
    "telangana":     "Hyderabad",
    "hyderabad":     "Hyderabad",
    "bengaluru":     "Bengaluru",
    "bangalore":     "Bengaluru",
    "karnataka":     "Bengaluru",
    "andhra pradesh":"Andhra Pradesh",
    "kurnool":       "Kurnool",
    "bhimavaram":    "Bhimavaram",
    "suryapet":      "Suryapet",
}


def _normalise_city(raw: str) -> str:
    low = raw.lower().strip()
    for key, city in _CITY_ALIASES.items():
        if key in low:
            return city
    return raw.strip().title()


def _slug_to_id(url_path: str) -> str:
    """Convert a URL path like '/projects/ark-kushak' → 'ark-kushak'."""
    return url_path.rstrip("/").split("/")[-1]


# ── HTML fetching (plain requests — works for Astro static-rendered pages) ──
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})


def _fetch_html(url: str) -> str | None:
    try:
        resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
        log.warning("ARK scraper: HTTP %s for %s", resp.status_code, url)
    except Exception as exc:
        log.warning("ARK scraper: fetch error for %s — %s", url, exc)
    return None


# ── Link discovery ────────────────────────────────────────────────────────────
def _discover_project_urls(html: str) -> list[str]:
    """Extract unique project page URLs from the landing page HTML."""
    seen: set[str] = set()
    urls: list[str] = []

    # Match href attributes that point to a project path
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        # Normalise relative → absolute
        if href.startswith("http"):
            absolute = href
        elif href.startswith("/"):
            absolute = ARK_BASE_URL + href
        else:
            continue

        parsed = urlparse(absolute)
        if parsed.netloc != "arkgroup.in":
            continue

        path = parsed.path.rstrip("/") + "/"
        if any(path.startswith(pfx) for pfx in PROJECT_PATH_PREFIXES):
            if absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)

    log.info("ARK scraper: discovered %d project URLs", len(urls))
    return urls


# ── Per-project parsing ───────────────────────────────────────────────────────
# We deliberately avoid a full BeautifulSoup parse tree so we don't need that
# dependency — plain regex on the pre-rendered HTML is sufficient because the
# ARK site uses Astro (static HTML output).

def _extract_text(pattern: str, html: str, default: str = "") -> str:
    m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if not m:
        return default
    raw = m.group(1)
    # Strip HTML tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    # Collapse whitespace
    return " ".join(raw.split()).strip()


def _extract_meta(name: str, html: str) -> str:
    """Pull a <meta name="..." content="..."> value."""
    pattern = rf'<meta\s+(?:name|property)=["\'](?:og:)?{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    m = re.search(pattern, html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Also try content first
    pattern2 = rf'<meta\s+content=["\']([^"\']+)["\']\s+(?:name|property)=["\'](?:og:)?{re.escape(name)}["\']'
    m2 = re.search(pattern2, html, re.IGNORECASE)
    return m2.group(1).strip() if m2 else ""


def _extract_title(html: str) -> str:
    m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        # Strip site suffix like "| Ark Group"
        raw = re.sub(r"\s*\|\s*Ark Group.*$", "", raw, flags=re.IGNORECASE)
        return raw.strip()
    return ""


def _extract_price(html: str) -> str:
    """Try to find a price mention in the HTML."""
    patterns = [
        r"(?:Starting|Price|from|onwards|at)\s*(?:at\s*)?₹\s*([\d,.]+\s*(?:Cr|L|Lac|Lakh)?)",
        r"₹\s*([\d,.]+\s*(?:Cr|L|Lac|Lakh)?)",
        r"([\d,.]+\s*(?:Crore|Lakhs?|Cr|L))\s*onwards",
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            raw = m.group(0).strip()
            return raw if raw else m.group(1).strip()
    return "Price on Request"


def _extract_bhk(html: str) -> str:
    """Extract BHK / configuration info."""
    m = re.search(
        r"(\d\s*(?:&amp;|&|and|,)?\s*\d?\s*BHK|[Gg]\+\d\s+(?:Villa|Villas?))\b",
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(0).replace("&amp;", "&").strip()
    return "Residential"


def _extract_location(html: str, project_name: str, page_title: str = "") -> tuple[str, str]:
    """Return (locality, city) strings.

    Strategy (most-to-least reliable):
    1. Parse the page <title> — ARK always embeds city in title, e.g.
       "3BHK Apartments in Kurnool | Ark Raaga…"
    2. Check meta description for "in <City>" patterns.
    3. Project-name → locality lookup table.
    """
    locality = ""
    city = ""

    # ── 1. Title-based extraction (most reliable) ─────────────────────────────
    # Pattern: "... in <Locality>, <City>" or "... in <City>"
    title_lower = page_title.lower()
    TITLE_CITY_MAP = [
        ("kurnool",         "Kurnool",     "Kurnool"),
        ("bhimavaram",      "Bhimavaram",  "Bhimavaram"),
        ("suryapet",        "Suryapet",    "Suryapet"),
        ("whitefield",      "Whitefield",  "Bengaluru"),
        ("bengaluru",       "Bengaluru",   "Bengaluru"),
        ("bangalore",       "Bengaluru",   "Bengaluru"),
        ("bachupally",      "Bachupally",  "Hyderabad"),
        ("uppal",           "Uppal",       "Hyderabad"),
        ("kondapur",        "Kondapur",    "Hyderabad"),
        ("kharmanghat",     "Kharmanghat", "Hyderabad"),
        ("kongara kalan",   "Kongara Kalan","Hyderabad"),
        ("kongara",         "Kongara Kalan","Hyderabad"),
        ("hitec city",      "HITEC City",  "Hyderabad"),
        ("hyderabad",       "Hyderabad",   "Hyderabad"),
    ]
    for keyword, loc, cty in TITLE_CITY_MAP:
        if keyword in title_lower:
            locality = loc
            city = cty
            break

    # ── 2. Meta description pattern ───────────────────────────────────────────
    if not city:
        desc = _extract_meta("description", html) or _extract_meta("og:description", html)
        desc_lower = desc.lower()
        for keyword, loc, cty in TITLE_CITY_MAP:
            if keyword in desc_lower:
                locality = loc
                city = cty
                break

    # ── 3. Project-name → locality table ─────────────────────────────────────
    if not locality:
        name_lower = project_name.lower()
        SLUG_MAP = [
            ("kushak",      "Uppal",           "Hyderabad"),
            ("samyak",      "Bachupally",       "Hyderabad"),
            ("oak tree",    "Whitefield",       "Bengaluru"),
            ("mukunda",     "Suryapet",         "Suryapet"),
            ("cloud city",  "Whitefield",       "Bengaluru"),
            ("serene",      "Whitefield",       "Bengaluru"),
            ("hamptons",    "Kondapur",         "Hyderabad"),
            ("aptha",       "Kharmanghat",      "Hyderabad"),
            ("raaga",       "Kurnool",          "Kurnool"),
            ("mayuri",      "Kurnool",          "Kurnool"),
            ("pride",       "Bhimavaram",       "Bhimavaram"),
            ("aryama",      "Kongara Kalan",    "Hyderabad"),
            ("artha",       "Kongara Kalan",    "Hyderabad"),
        ]
        for slug, loc, cty in SLUG_MAP:
            if slug in name_lower:
                locality = loc
                city = cty
                break

    if not locality:
        locality = city or "Hyderabad"
    if not city:
        city = _normalise_city(locality)

    return locality.strip(), _normalise_city(city)


def _extract_amenities(html: str) -> list[str]:
    """Return a deduplicated list of amenity strings."""
    amenity_keywords = [
        "Swimming Pool", "Club House", "Clubhouse", "Gym", "Garden", "Security",
        "Kids Play Area", "Multipurpose Hall", "EV Charging", "Lobby",
        "Amphitheatre", "Power Backup", "Yoga", "Aerobics", "Indoor Games",
        "Cricket Net", "Basketball", "Jogging Track", "Solar", "Lift",
        "Intercom", "Rain Water", "IGBC", "Community Hall",
    ]
    found: list[str] = []
    seen: set[str] = set()
    for kw in amenity_keywords:
        if kw.lower() in html.lower() and kw not in seen:
            seen.add(kw)
            found.append(kw)
    return found


def _extract_description(html: str) -> str:
    """Best-effort description extraction."""
    # 1. Try meta description
    desc = _extract_meta("description", html)
    if desc and len(desc) > 40:
        return desc

    # 2. Try og:description
    og = _extract_meta("og:description", html)
    if og and len(og) > 40:
        return og

    # 3. Pull first substantial paragraph
    m = re.search(r"<p[^>]*>([^<]{80,500})</p>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return " ".join(m.group(1).split())

    return "Premium residential project by ARK Group."


def _extract_tag(html: str, name: str) -> str:
    low_html = html.lower()
    low_name = name.lower()
    if "luxury" in low_name or "exclusive" in low_html or "ultra-premium" in low_html:
        return "Luxury"
    if "ready to move" in low_html or "completed" in low_html:
        return "Ready to Move"
    if "igbc" in low_html or "gold" in low_html:
        return "Premium"
    return "Premium"


def _parse_project(url: str, html: str) -> dict | None:
    """Parse a single project page HTML into an Apartment-compatible dict."""
    name = _extract_title(html)
    if not name:
        log.warning("ARK scraper: could not extract title from %s", url)
        return None

    locality, city = _extract_location(html, name)
    lat, lng = geocode_location(locality, city)
    price = _extract_price(html)
    bhk   = _extract_bhk(html)
    desc  = _extract_description(html)
    tag   = _extract_tag(html, name)
    amenities = _extract_amenities(html)
    slug  = _slug_to_id(urlparse(url).path)

    return {
        "id":          f"ark-{slug}",
        "name":        name,
        "price":       price,
        "bhk":         bhk,
        "address":     f"{locality}, {city}",
        "description": desc,
        "city":        city,
        "locality":    locality,
        "latitude":    lat,
        "longitude":   lng,
        "tag":         tag,
        "amenities":   amenities,
        "source":      "ARK Group",
        "source_url":  url,
    }


# ── Public API ────────────────────────────────────────────────────────────────
def _do_scrape() -> list[dict]:
    """Full scrape: landing page → project URLs → parse each page."""
    log.info("ARK scraper: starting full scrape of %s", ARK_LANDING_PAGE)
    landing_html = _fetch_html(ARK_LANDING_PAGE)
    if not landing_html:
        log.error("ARK scraper: could not fetch landing page")
        return []

    urls = _discover_project_urls(landing_html)
    if not urls:
        log.warning("ARK scraper: no project URLs discovered")
        return []

    projects: list[dict] = []
    for url in urls:
        html = _fetch_html(url)
        if not html:
            continue
        project = _parse_project(url, html)
        if project:
            projects.append(project)
            log.info("ARK scraper: parsed project '%s' (%s)", project["name"], project["city"])

    log.info("ARK scraper: finished — %d projects loaded", len(projects))
    return projects


def get_ark_projects(force: bool = False) -> list[dict]:
    """Return all ARK Group projects. Uses in-memory cache (TTL = 6 h).

    Parameters
    ----------
    force : bool
        If True, bypass cache and re-scrape immediately.
    """
    global _cached_projects, _cache_timestamp

    with _cache_lock:
        age = time.time() - _cache_timestamp
        if not force and _cached_projects is not None and age < CACHE_TTL_SECONDS:
            return _cached_projects

        projects = _do_scrape()
        _cached_projects = projects
        _cache_timestamp = time.time()
        return projects


def get_ark_projects_for_city(city: str | None) -> list[dict]:
    """Return ARK projects filtered by city name (case-insensitive)."""
    all_projects = get_ark_projects()
    if not city:
        return all_projects
    normalized = city.strip().lower()
    return [p for p in all_projects if p["city"].lower() == normalized]


def get_ark_inventory_context(city: str | None) -> str:
    """Build an LLM-ready inventory block for ARK projects in a given city."""
    projects = get_ark_projects_for_city(city)
    if not projects:
        return ""

    city_label = city or "all cities"
    lines = [
        (
            f"- {p['name']} (ARK Group — {p.get('tag', 'Premium')}): "
            f"{p['bhk']}, {p['price']}, {p['address']}. {p['description']}"
        )
        for p in projects
    ]

    return (
        f"ACTIVE CITY: {city_label}\n"
        f"ARK Group projects in {city_label} — PRIORITISE these options:\n"
        + "\n".join(lines)
        + "\n\nWhen the user asks about this city, list ALL ARK Group properties above "
        "with name, BHK, and price. Mention that these are ARK Group projects. "
        "Do not invent properties outside this list."
    )
