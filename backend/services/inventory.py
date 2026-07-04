"""Property inventory helpers for LLM context.

Priority:  ARK Group projects → static APARTMENTS fallback
"""

from data.apartments import APARTMENTS
from services.ark_scraper import get_ark_inventory_context, get_ark_projects_for_city

CITY_KEYWORDS: dict[str, str] = {
    # Existing cities
    "pune":       "Pune",
    "mumbai":     "Mumbai",
    "delhi":      "Delhi",
    "new delhi":  "Delhi",
    "hyderabad":  "Hyderabad",
    "gurugram":   "Delhi",
    "gurgaon":    "Delhi",
    "ncr":        "Delhi",
    # ARK Group cities
    "bengaluru":  "Bengaluru",
    "bangalore":  "Bengaluru",
    "whitefield": "Bengaluru",
    "kurnool":    "Kurnool",
    "bhimavaram": "Bhimavaram",
    "suryapet":   "Suryapet",
    "bachupally": "Hyderabad",
    "uppal":      "Hyderabad",
    "kondapur":   "Hyderabad",
    "hitec city": "Hyderabad",
    "gachibowli": "Hyderabad",
    "telangana":  "Hyderabad",
    "kongara":    "Hyderabad",
    "kharmanghat":"Hyderabad",
}


def detect_city_from_text(text: str) -> str | None:
    lowered = text.lower()
    # Sort by keyword length descending so "new delhi" beats "delhi"
    for keyword, city in sorted(CITY_KEYWORDS.items(), key=lambda item: -len(item[0])):
        if keyword in lowered:
            return city
    return None


def get_apartments_for_city(city: str | None) -> list[dict]:
    """Return apartments for a city, ARK projects take priority.

    If ARK Group has projects in this city → return those.
    Otherwise fall back to the static APARTMENTS dataset.
    """
    ark = get_ark_projects_for_city(city)
    if ark:
        return ark

    # Static fallback
    if not city:
        return APARTMENTS
    normalized = city.strip().lower()
    return [a for a in APARTMENTS if a["city"].lower() == normalized]


def build_inventory_context(city: str | None) -> str:
    """Build LLM system-prompt block for the given city.

    Uses ARK Group inventory when available; falls back to static data.
    """
    # Try ARK first
    ark_context = get_ark_inventory_context(city)
    if ark_context:
        return ark_context

    # Static fallback
    listings = get_apartments_for_city(city)
    if not listings:
        return ""

    city_label = city or "all cities"
    lines = [
        (
            f"- {item['name']} ({item.get('tag', 'Listed')}): "
            f"{item['bhk']}, {item['price']}, {item['address']}. {item['description']}"
        )
        for item in listings
    ]

    return (
        f"ACTIVE CITY: {city_label}\n"
        f"Available properties in {city_label} — ONLY mention these exact options:\n"
        + "\n".join(lines)
        + "\n\nWhen the user asks about this city, list ALL properties above with name, BHK, and price. "
        "Do not invent properties outside this list."
    )
