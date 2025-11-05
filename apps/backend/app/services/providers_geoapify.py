import logging
from typing import Any

import httpx
from app.core.config import DEBUG, GEOAPIFY_API_BASE, GEOAPIFY_API_KEY

logger = logging.getLogger("travxy.provider.geoapify")
if DEBUG:
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        h = logging.StreamHandler()
        logger.addHandler(h)

# NOTE:
# Categories here are aligned with Geoapify's taxonomy.
# Docs: https://apidocs.geoapify.com/docs/places/
#
# Key points:
# - There is NO "tourism.museum" category; museums typically fall under
#   tourism.sights / tourism.attraction depending on OSM data.
# - Geoapify requires at least one of [type, categories], so we ALWAYS send categories.
# - If a 400 occurs (invalid categories for that region), we retry with a minimal, safe set.

SAFE_CATEGORIES_MAP: dict[str, list[str]] = {
    # Food
    "catering": [
        "catering",
        "catering.restaurant",
        "catering.cafe",
        "catering.fast_food",
        "catering.food_court",
    ],
    # Viewpoints / attractions
    "view_points": [
        "tourism.attraction",
        "tourism.attraction.viewpoint",
        "tourism.sights",
    ],
    # Culture & history (avoid memorial spam)
    "cultural": [
        "tourism.attraction",
        "tourism.sights",
        "tourism.sights.castle",
        "tourism.sights.tower",
        "tourism.sights.archaeological_site",
        "tourism.sights.place_of_worship",
    ],
    "museums": [
        "tourism.attraction",
        "tourism.sights",
    ],
    "architecture": [
        "building",
        "tourism.sights",
        "tourism.attraction",
    ],
    "historic": [
        "tourism.sights",
        "tourism.sights.castle",
        "tourism.sights.archaeological_site",
        "tourism.attraction",
    ],
    # Nature — use documented dot categories
    "natural": [
        "leisure.park",  # valid “park”
        "leisure.park.nature_reserve",
        "natural.forest",
        "natural.protected_area",
        "national_park",  # own top-level category
        # keep broad tourism buckets so you get nice “natural sights”
        "tourism.sights",
        "tourism.attraction",
    ],
}

# Minimal safe set used when mapping is empty or rejected with 400
FALLBACK_CATEGORIES: list[str] = [
    "tourism.attraction",
    "tourism.sights",
    "catering",
]


def _compose_categories_from_kinds(kinds_csv: str | None) -> list[str]:
    if not kinds_csv:
        return []
    out: list[str] = []
    for t in kinds_csv.split(","):
        t = t.strip().lower()
        out.extend(SAFE_CATEGORIES_MAP.get(t, []))
    # dedupe preserve order
    seen = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def _params_with_categories(
    *, lon: float, lat: float, radius_m: int, limit: int, categories: list[str]
) -> dict[str, Any]:
    return {
        "apiKey": GEOAPIFY_API_KEY,
        "limit": limit,
        "filter": f"circle:{lon},{lat},{radius_m}",
        "bias": f"proximity:{lon},{lat}",
        "categories": ",".join(categories),
    }


class GeoapifyProvider:
    async def search_radius(
        self, *, lon: float, lat: float, radius_m: int, kinds: str | None, limit: int
    ) -> list[dict[str, Any]]:
        if not GEOAPIFY_API_KEY:
            return []

        url = f"{GEOAPIFY_API_BASE}/places"

        categories = _compose_categories_from_kinds(kinds)
        if not categories:
            categories = FALLBACK_CATEGORIES[:]

        params = _params_with_categories(
            lon=lon, lat=lat, radius_m=radius_m, limit=limit, categories=categories
        )

        if DEBUG:
            log_params = {**params, "apiKey": "****"}
            logger.debug(f"[Geoapify] GET {url} params={log_params}")

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
            if DEBUG:
                logger.debug(
                    f"[Geoapify] {r.status_code} len={len(r.text)} body[:300]={r.text[:300]}"
                )

            # Retry with minimal safe set if categories are rejected
            if r.status_code == 400:
                if DEBUG:
                    logger.debug("[Geoapify] 400 -> retry with FALLBACK_CATEGORIES only")
                params_fb = _params_with_categories(
                    lon=lon,
                    lat=lat,
                    radius_m=radius_m,
                    limit=limit,
                    categories=FALLBACK_CATEGORIES,
                )
                r = await client.get(url, params=params_fb)
                if DEBUG:
                    logger.debug(
                        f"[Geoapify] RETRY {r.status_code} len={len(r.text)} body[:300]={r.text[:300]}"
                    )

            r.raise_for_status()
            data = r.json() or {}

        feats = data.get("features") or []
        out: list[dict[str, Any]] = []
        for f in feats:
            prop = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates") or [None, None]
            lon2, lat2 = coords[0], coords[1]
            if lon2 is None or lat2 is None:
                continue

            cats = prop.get("categories")
            if isinstance(cats, list):
                kinds_str = ",".join(cats)
            else:
                kinds_str = str(cats or "")

            out.append(
                {
                    "xid": str(
                        prop.get("place_id")
                        or prop.get("datasource", {}).get("raw", {}).get("id")
                        or ""
                    ),
                    "name": prop.get("name") or prop.get("formatted") or "Unnamed place",
                    "kinds": kinds_str,
                    "point": {"lon": float(lon2), "lat": float(lat2)},
                    # carry distance for later sorting (present when using bias)
                    "distance": prop.get("distance"),
                }
            )
        return out

    async def place_details(self, xid: str) -> dict[str, Any]:
        # For MVP we don't fetch extra details from Geoapify.
        return {}
