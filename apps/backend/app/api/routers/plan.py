import logging
import math
from collections.abc import Iterable
from typing import Any, Literal

import httpx
from app.core.config import (
    DEFAULT_SEARCH_RADIUS_M,
    GEOAPIFY_API_KEY,
    GOOGLE_MAPS_API_KEY,
    OPENTRIPMAP_API_KEY,
    PLACES_FALLBACK,
)
from app.schemas.candidates import Candidate, CandidatesResponse
from app.schemas.itinerary import ItineraryBuildRequest, ItineraryResponse
from app.schemas.trip import TripRequest
from app.services.itinerary_planner import build_itinerary
from app.services.opentripmap import compose_kinds, extract_origin
from app.services.providers_factory import build_providers_chain
from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger("travxy.router.plan")

ActivityType = Literal["hiking", "restaurant", "attraction", "viewpoint", "lodging"]
_MIN_RADIUS_M = 500
_MAX_RADIUS_M = 50000


def _clamp_radius_m(v: int) -> int:
    return int(min(max(int(v), _MIN_RADIUS_M), _MAX_RADIUS_M))


# Geoapify categories look like: "tourism.sights", "catering.restaurant", etc.
_ALLOWED_CATEGORY_PREFIXES = (
    "tourism.attraction",
    "tourism.sights",
    "catering",
)

# Drop roads/addresses outright (Geoapify)
_BLOCKED_CATEGORY_PREFIXES = (
    "highway.",
    "address.",
)


def _iter_tokens(*parts: str) -> Iterable[str]:
    for p in parts:
        s = (p or "").strip().lower()
        if not s:
            continue
        for token in s.replace("|", ",").replace("/", ",").replace(";", ",").split(","):
            t = token.strip()
            if not t:
                continue
            yield t
            # Geoapify taxonomy uses dotted paths like "tourism.sights".
            if "." in t:
                for sub in t.split("."):
                    sub = sub.strip()
                    if sub:
                        yield sub


def infer_activity_type(
    kinds_str: str,
    *,
    categories: Iterable[str] | None = None,
    name: str | None = None,
) -> ActivityType:
    """
    Works for:
    - OpenTripMap kinds: "view_points", "natural", "catering", ...
    - Geoapify categories: "tourism.sights", "catering.restaurant", ...
    - Google types: "tourist_attraction", "restaurant", "park", "viewpoint", ...
    """
    cat_list = list(categories or [])
    tokens = set(_iter_tokens(kinds_str, *(str(c) for c in cat_list), name or ""))

    scores: dict[ActivityType, int] = {
        "restaurant": 0,
        "viewpoint": 0,
        "hiking": 0,
        "attraction": 0,
        "lodging": 0,
    }

    def has_any(opts: Iterable[str]) -> bool:
        return any(o in tokens for o in opts)

    # Viewpoints
    if has_any({"view_points", "viewpoint", "observation_deck", "observation_tower", "lookout"}):
        scores["viewpoint"] += 5
    if has_any({"scenic", "panorama"}):
        scores["viewpoint"] += 2

    # Food
    if has_any(
        {
            "restaurant",
            "cafe",
            "coffee_shop",
            "bakery",
            "bar",
            "breakfast_restaurant",
            "brunch_restaurant",
            "fast_food_restaurant",
            "pizza_restaurant",
            "sandwich_shop",
            "ice_cream_shop",
            "food_court",
            "catering",
        }
    ):
        scores["restaurant"] += 5

    # Lodging
    if has_any({"lodging", "hotel", "resort_hotel", "guest_house", "bed_and_breakfast"}):
        scores["lodging"] += 4
    if "campground" in tokens:
        scores["lodging"] += 2
        scores["hiking"] += 1

    # Attractions
    if has_any({"tourist_attraction", "museum", "historical_landmark", "historical_place"}):
        scores["attraction"] += 4
    if has_any({"art_gallery", "cultural_center", "performing_arts_theater", "planetarium"}):
        scores["attraction"] += 3
    if has_any({"visitor_center", "tourist_information_center"}):
        scores["attraction"] += 2
    if has_any({"market", "plaza"}):
        scores["attraction"] += 2

    # Nature / hiking (parks are a weak signal; hiking_area/trail/national_park are strong)
    if has_any({"hiking_area", "trail", "national_park", "protected_area", "natural_feature"}):
        scores["hiking"] += 4
    if has_any({"park", "forest"}):
        scores["hiking"] += 1
    if "sports_activity_location" in tokens:
        scores["hiking"] += 1

    # Business/service places shouldn't win as hikes just because they offer tours.
    if has_any({"tour_agency", "travel_agency", "car_rental", "corporate_office"}):
        scores["attraction"] += 2
        scores["hiking"] -= 3
        scores["viewpoint"] -= 2

    if max(scores.values()) <= 0:
        return "attraction"

    best = max(scores.items(), key=lambda kv: kv[1])[0]

    # Guardrails / tie-breaks
    if best == "hiking" and scores["hiking"] <= 1 and scores["attraction"] >= 1:
        # Avoid classifying city squares/urban places as hikes just because of "park".
        return "attraction"
    if best == "lodging" and scores["restaurant"] >= scores["lodging"]:
        # For MVP, treat hotel+restaurant as restaurant if food signal exists.
        return "restaurant"

    return best


def _is_noise(kinds_str: str, name: str) -> bool:
    k = (kinds_str or "").lower()
    nm = (name or "").lower().strip()

    # City-center noise
    if any(bad in k for bad in ["memorial", "plaque", "address_point"]):
        return True

    # Very short names are often junk
    if len(nm) <= 3:
        return True

    # Some extremely generic labels can be junk
    if nm in {"atm", "wc", "toilet", "parking", "parking lot"}:
        return True

    return False


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _normalize_categories(item: dict[str, Any]) -> list[str]:
    cats_raw = item.get("categories") or item.get("kinds") or ""
    if isinstance(cats_raw, str):
        return [c.strip() for c in cats_raw.split(",") if c.strip()]
    if isinstance(cats_raw, list):
        return [str(c).strip() for c in cats_raw if str(c).strip()]
    return []


def _looks_like_google(item: dict[str, Any]) -> bool:
    # Your GooglePlacesProvider currently includes these keys
    return ("user_ratings_total" in item) or ("rating" in item)


def _looks_like_geoapify(item: dict[str, Any], cats: list[str]) -> bool:
    # Geoapify categories usually have dot prefixes: tourism.*, catering.*, leisure.*, natural.*
    if not cats:
        return False
    return any(
        c.startswith(("tourism.", "catering.", "leisure.", "natural.", "building", "national_park"))
        for c in cats
    )


def _sort_key(it: dict[str, Any]) -> tuple[float, float, int]:
    """
    Prefer:
    - closer distance
    - higher rating (Google)
    - more ratings (Google)
    """
    dist = float(it.get("distance") or 1e12)
    rating = float(it.get("rating") or 0.0)
    rating_count = int(it.get("user_ratings_total") or 0)

    # For sorting ascending: negative rating/rating_count to prefer higher values
    return (dist, -rating, -rating_count)


async def _fetch_places(
    *,
    lon: float,
    lat: float,
    kinds: str | None,
    radius_m: int,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    providers = build_providers_chain()
    errors: list[str] = []
    provider_used: str | None = None

    raw: list[dict[str, Any]] = []
    for provider in providers:
        try:
            raw = await provider.search_radius(
                lon=lon, lat=lat, radius_m=radius_m, kinds=kinds, limit=limit
            )
            if raw:
                provider_used = provider.__class__.__name__
                break
        except Exception as e:
            errors.append(f"{provider.__class__.__name__}: {e}")
        if not PLACES_FALLBACK:
            break

    return raw, provider_used, errors


def _clean_places(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen_names_coords: set[tuple[str, float, float]] = set()

    for item in raw:
        point = item.get("point") or {}
        if "lon" not in point or "lat" not in point:
            continue

        name = (item.get("name") or "Unnamed place").strip()
        kinds_str = item.get("kinds") or ""
        cats = _normalize_categories(item)

        # Only apply Geoapify taxonomy filters to Geoapify-like items.
        # Important: do NOT apply these filters to Google types (they will remove valid places).
        if _looks_like_geoapify(item, cats) and not _looks_like_google(item):
            if any(c.startswith(_BLOCKED_CATEGORY_PREFIXES) for c in cats):
                continue
            if cats and not any(c.startswith(_ALLOWED_CATEGORY_PREFIXES) for c in cats):
                continue

        # Basic name noise guard
        if _is_noise(kinds_str, name):
            continue
        if name.lower().startswith(("rue ", "avenue ", "boulevard ", "place ")):
            continue

        lon2 = float(point["lon"])
        lat2 = float(point["lat"])
        key_name = name.lower()

        # Exact signature
        sig = (key_name, round(lon2, 6), round(lat2, 6))
        if sig in seen_names_coords:
            continue

        # Proximity duplicate: same name within 75m
        is_near_dup = False
        for existing in cleaned:
            if (existing.get("name") or "").lower() == key_name:
                kp = existing.get("point") or {}
                if "lon" in kp and "lat" in kp:
                    if _haversine_m(lon2, lat2, float(kp["lon"]), float(kp["lat"])) <= 75:
                        is_near_dup = True
                        break
        if is_near_dup:
            continue

        seen_names_coords.add(sig)
        item["categories"] = cats
        cleaned.append(item)

    cleaned.sort(key=_sort_key)
    for it in cleaned:
        it["_inferred_type"] = infer_activity_type(
            it.get("kinds") or "",
            categories=it.get("categories") or [],
            name=it.get("name") or "",
        )
    return cleaned


@router.post("/plan_trip", response_model=CandidatesResponse)
async def plan_trip(req: TripRequest, limit: int = 200) -> CandidatesResponse:
    if not (GOOGLE_MAPS_API_KEY or GEOAPIFY_API_KEY or OPENTRIPMAP_API_KEY):
        raise HTTPException(
            status_code=500,
            detail="No places provider configured. Please set GOOGLE_MAPS_API_KEY, GEOAPIFY_API_KEY, or OPENTRIPMAP_API_KEY.",
        )

    try:
        lon, lat = extract_origin(req.geometry)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    kinds = compose_kinds(req.interests)
    logger.warning(
        f"[PLAN] origin=({lon:.5f},{lat:.5f}) interests={req.interests} -> kinds={kinds}"
    )

    safe_limit = int(min(max(limit, 1), 200))
    radius_m = (
        _clamp_radius_m(req.radius_m) if req.radius_m is not None else DEFAULT_SEARCH_RADIUS_M
    )
    raw, provider_used, errors = await _fetch_places(
        lon=lon, lat=lat, kinds=kinds, radius_m=radius_m, limit=safe_limit
    )
    cleaned = _clean_places(raw)

    out: list[Candidate] = []
    for it in cleaned:
        p = it.get("point") or {}
        lon_raw = p.get("lon")
        lat_raw = p.get("lat")
        if lon_raw is None or lat_raw is None:
            # Shouldn't happen due to _clean_places(), but keeps types and runtime safe.
            continue
        try:
            lon_f = float(lon_raw)
            lat_f = float(lat_raw)
        except (TypeError, ValueError):
            continue

        dist_raw = it.get("distance")
        rating_raw = it.get("rating")
        urt_raw = it.get("user_ratings_total")
        inferred = it.get("_inferred_type") or infer_activity_type(
            it.get("kinds") or "",
            categories=it.get("categories") or [],
            name=it.get("name") or "",
        )
        out.append(
            Candidate(
                xid=str(it.get("xid") or "") or None,
                name=str(it.get("name") or "Unnamed place"),
                location=[lon_f, lat_f],
                distance_m=float(dist_raw) if dist_raw is not None else None,
                inferred_type=inferred,
                kinds=str(it.get("kinds") or "") or None,
                categories=[str(c) for c in (it.get("categories") or [])],
                rating=float(rating_raw) if rating_raw is not None else None,
                user_ratings_total=int(urt_raw) if urt_raw is not None else None,
                provider=provider_used,
            )
        )

    return CandidatesResponse(
        query={
            "days": req.days,
            "pace": req.pace,
            "interests": req.interests,
            "travel_mode": req.travel_mode,
            "geometry_type": req.geometry.get("type"),
            "radius_m": radius_m,
            "provider": provider_used,
            "raw_count": len(raw),
            "cleaned_count": len(cleaned),
            "limit": safe_limit,
            "errors": errors,
        },
        candidates=out,
        status="ok",
    )


@router.post("/build_itinerary", response_model=ItineraryResponse)
async def build_itinerary_route(req: ItineraryBuildRequest) -> ItineraryResponse:
    # This endpoint is the "AI stage": it takes TripRequest + candidates and builds a day plan.
    try:
        return await build_itinerary(req)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail=(
                    "LLM rate-limited (429). Wait a bit and try again, or check your OpenAI "
                    "billing/usage limits."
                ),
            )
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
