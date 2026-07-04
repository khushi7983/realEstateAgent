"""Properties REST endpoints.

Search priority:
  1. ARK Group projects (scraped from arkgroup.in, cached 6 h)
  2. Static APARTMENTS dataset (fallback when no ARK projects for that city)
"""

from fastapi import APIRouter, HTTPException

from data.apartments import APARTMENTS
from services.ark_scraper import get_ark_projects, get_ark_projects_for_city

router = APIRouter(prefix="/properties", tags=["properties"])


def _get_all_apartments() -> list[dict]:
    """Merge ARK + static data, deduplicating by id.  ARK entries win."""
    ark = get_ark_projects()
    ark_ids = {p["id"] for p in ark}
    static = [a for a in APARTMENTS if a["id"] not in ark_ids]
    return ark + static


@router.get("/apartments")
async def list_apartments(city: str | None = None):
    """Return apartments, ARK Group projects take priority.

    - If city is provided and ARK has projects there → return ARK projects only.
    - If city is provided and ARK has none → fall back to static dataset.
    - If no city → return ARK + static combined.
    """
    if city:
        ark = get_ark_projects_for_city(city)
        if ark:
            return {"city": city, "apartments": ark, "count": len(ark), "source": "ARK Group"}

        # Fallback to static
        normalized = city.strip().lower()
        filtered = [a for a in APARTMENTS if a["city"].lower() == normalized]
        return {"city": city, "apartments": filtered, "count": len(filtered), "source": "static"}

    # No city filter — return everything merged
    all_apts = _get_all_apartments()
    return {"city": None, "apartments": all_apts, "count": len(all_apts)}


@router.get("/apartments/{apartment_id}")
async def get_apartment(apartment_id: str):
    """Look up a single apartment by id (checks ARK first, then static)."""
    # Check ARK projects
    for project in get_ark_projects():
        if project["id"] == apartment_id:
            return project

    # Check static data
    for apartment in APARTMENTS:
        if apartment["id"] == apartment_id:
            return apartment

    raise HTTPException(status_code=404, detail="Apartment not found")


@router.post("/ark/refresh")
async def refresh_ark_cache():
    """Force a fresh scrape of the ARK Group website. Returns project count."""
    projects = get_ark_projects(force=True)
    return {
        "message": "ARK Group cache refreshed",
        "count": len(projects),
        "projects": [{"id": p["id"], "name": p["name"], "city": p["city"]} for p in projects],
    }
