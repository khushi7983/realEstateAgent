"""Hardcoded geocode lookup for ARK Group project locations.

Keys are normalized lower-case locality strings.
Lat/Lng values are accurate for each neighbourhood/city.
"""

ARK_GEOCODES: dict[str, tuple[float, float]] = {
    # Hyderabad sub-localities
    "uppal":           (17.4081, 78.5594),
    "bachupally":      (17.5364, 78.3748),
    "kondapur":        (17.4616, 78.3643),
    "kharmanghat":     (17.3576, 78.5477),
    "kongara kalan":   (17.3254, 78.4293),
    "hyderabad":       (17.3850, 78.4867),

    # Bengaluru
    "whitefield":      (12.9698, 77.7500),
    "bengaluru":       (12.9716, 77.5946),
    "bangalore":       (12.9716, 77.5946),

    # Andhra Pradesh
    "kurnool":         (15.8281, 78.0373),
    "bhimavaram":      (16.5449, 81.5212),

    # Telangana (non-Hyderabad)
    "suryapet":        (17.1384, 79.6226),
}


def geocode_location(locality: str, city: str) -> tuple[float, float]:
    """Return (lat, lng) for a given locality/city string.

    Tries locality first, then city, then a central India fallback.
    """
    for key in (locality.lower().strip(), city.lower().strip()):
        if key in ARK_GEOCODES:
            return ARK_GEOCODES[key]

    # Partial match — e.g. "Whitefield, Bengaluru" should hit "whitefield"
    combined = f"{locality} {city}".lower()
    for key, coords in ARK_GEOCODES.items():
        if key in combined:
            return coords

    # Generic fallback — central India
    return (20.5937, 78.9629)
