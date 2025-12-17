import logging
import math
import re
from typing import Any

import httpx
from app.core.config import DEBUG, GOOGLE_MAPS_API_KEY, GOOGLE_PLACES_API_BASE

logger = logging.getLogger("travxy.provider.google_places")
if DEBUG:
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# A conservative allowlist for Places API v1 nearby "includedTypes".
# If Google changes/limits types per endpoint, we also have a retry that removes unsupported ones.
_GOOGLE_TYPES_ALLOWLIST: set[str] = {
    "restaurant",
    "cafe",
    "bar",
    "bakery",
    "meal_takeaway",
    "meal_delivery",
    "tourist_attraction",
    "museum",
    "park",
    "campground",
    "hiking_area",
    "zoo",
    "aquarium",
    "amusement_park",
    "shopping_mall",
    "spa",
}


def _types_from_kinds(kinds_csv: str | None) -> list[str]:
    """
    Map OTM-style tokens into Google Place types for Nearby Search.
    IMPORTANT: Only return types that are likely supported by Places API v1 searchNearby.
    """
    if not kinds_csv:
        return ["tourist_attraction", "park", "restaurant", "cafe", "museum"]

    kinds = {k.strip().lower() for k in kinds_csv.split(",") if k.strip()}
    out: list[str] = []

    if "catering" in kinds:
        out += ["restaurant", "cafe", "bar", "bakery"]

    if "view_points" in kinds:
        # "viewpoint" is NOT accepted by v1 searchNearby in your project.
        # Use tourist_attraction/park instead.
        out += ["tourist_attraction", "park"]

    if "natural" in kinds:
        # "natural_feature" was rejected. Use park/campground/hiking_area.
        out += ["park", "hiking_area", "campground"]

    if any(k in kinds for k in ["cultural", "museums", "architecture", "historic"]):
        out += ["museum", "tourist_attraction"]

    # Filter by allowlist + dedupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t in _GOOGLE_TYPES_ALLOWLIST and t not in seen:
            uniq.append(t)
            seen.add(t)

    return uniq or ["tourist_attraction", "restaurant", "park"]


def _extract_unsupported_types(message: str) -> list[str]:
    # Example: "Unsupported types: natural_feature, point_of_interest, viewpoint."
    m = re.search(r"Unsupported types:\s*(.+?)(?:\.)?$", message or "")
    if not m:
        return []
    raw = m.group(1)
    return [t.strip() for t in raw.split(",") if t.strip()]


class GooglePlacesProvider:
    async def _search_nearby(
        self,
        *,
        lon: float,
        lat: float,
        radius_m: int,
        included_types: list[str],
        max_result_count: int,
    ) -> list[dict[str, Any]]:
        url = f"{GOOGLE_PLACES_API_BASE}/places:searchNearby"

        payload: dict[str, Any] = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": float(radius_m),
                }
            },
            "maxResultCount": int(min(max(max_result_count, 1), 20)),
            "includedTypes": included_types[:10],
            "rankPreference": "POPULARITY",
        }

        headers = {
            "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": ",".join(
                [
                    "places.id",
                    "places.displayName",
                    "places.location",
                    "places.types",
                    "places.primaryType",
                    "places.rating",
                    "places.userRatingCount",
                ]
            ),
        }

        async with httpx.AsyncClient(timeout=20) as client:
            if DEBUG:
                safe_headers = {**headers, "X-Goog-Api-Key": "****"}
                logger.debug(f"[GooglePlaces] POST {url} headers={safe_headers} payload={payload}")

            r = await client.post(url, headers=headers, json=payload)

            # If Google complains about unsupported types, remove them and retry once.
            if r.status_code == 400:
                try:
                    err = r.json().get("error", {})
                    msg = str(err.get("message") or "")
                except Exception:
                    msg = r.text or ""

                unsupported = _extract_unsupported_types(msg)
                if unsupported and payload.get("includedTypes"):
                    new_types = [t for t in payload["includedTypes"] if t not in set(unsupported)]
                    if DEBUG:
                        logger.debug(
                            f"[GooglePlaces] 400 unsupported={unsupported} -> retry with includedTypes={new_types}"
                        )
                    payload["includedTypes"] = new_types
                    r = await client.post(url, headers=headers, json=payload)

            if DEBUG:
                logger.debug(
                    f"[GooglePlaces] {r.status_code} len={len(r.text)} body[:300]={r.text[:300]}"
                )

            r.raise_for_status()
            data = r.json() or {}

        places = data.get("places") or []
        out: list[dict[str, Any]] = []

        for p in places:
            loc = p.get("location") or {}
            plat = loc.get("latitude")
            plon = loc.get("longitude")
            if plat is None or plon is None:
                continue

            name_obj = p.get("displayName") or {}
            name = name_obj.get("text") or "Unnamed place"

            types = p.get("types") or []
            if isinstance(types, list):
                kinds_str = ",".join(str(t) for t in types)
            else:
                kinds_str = str(types or "")

            distance_m = _haversine_m(lon, lat, float(plon), float(plat))

            out.append(
                {
                    "xid": str(p.get("id") or ""),
                    "name": name,
                    "kinds": kinds_str,
                    "categories": types if isinstance(types, list) else [],
                    "point": {"lon": float(plon), "lat": float(plat)},
                    "distance": distance_m,
                    "rating": p.get("rating"),
                    "user_ratings_total": p.get("userRatingCount"),
                }
            )

        out.sort(key=lambda it: it.get("distance") or 1e12)
        return out

    async def search_radius(
        self, *, lon: float, lat: float, radius_m: int, kinds: str | None, limit: int
    ) -> list[dict[str, Any]]:
        if not GOOGLE_MAPS_API_KEY:
            return []

        included_types = _types_from_kinds(kinds)

        # Calling searchNearby with many includedTypes returns a single ranked list (and is capped),
        # which can be unstable between nearby points. For better coverage per "kind", query each
        # includedType separately, then merge/dedupe.
        types = included_types[:10]
        if len(types) <= 1:
            return await self._search_nearby(
                lon=lon, lat=lat, radius_m=radius_m, included_types=types, max_result_count=limit
            )

        merged_by_id: dict[str, dict[str, Any]] = {}
        for t in types:
            chunk = await self._search_nearby(
                lon=lon, lat=lat, radius_m=radius_m, included_types=[t], max_result_count=20
            )
            for it in chunk:
                pid = str(it.get("xid") or "")
                if not pid:
                    continue
                if pid in merged_by_id:
                    existing = merged_by_id[pid]
                    existing_types = existing.get("categories") or []
                    new_types = it.get("categories") or []
                    if isinstance(existing_types, list) and isinstance(new_types, list):
                        existing["categories"] = sorted(
                            {*map(str, existing_types), *map(str, new_types)}
                        )
                        existing["kinds"] = ",".join(existing["categories"])
                    continue
                merged_by_id[pid] = it

        out = list(merged_by_id.values())
        out.sort(key=lambda it: it.get("distance") or 1e12)
        return out

    async def place_details(self, xid: str) -> dict[str, Any]:
        return {}
