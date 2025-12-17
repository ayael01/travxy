import logging
import math
from typing import Any, Literal

from app.core.config import (
    DEFAULT_SEARCH_RADIUS_M,
    GEOAPIFY_API_KEY,
    GOOGLE_MAPS_API_KEY,
    OPENTRIPMAP_API_KEY,
    PLACES_FALLBACK,
)
from app.schemas.trip import Activity, DayPlan, PlanResponse, TripRequest
from app.services.opentripmap import compose_kinds, extract_origin
from app.services.providers_factory import build_providers_chain
from app.utils.duration import default_visit_duration
from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger("travxy.router.plan")

ActivityType = Literal["hiking", "restaurant", "attraction", "viewpoint", "lodging"]

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


def infer_activity_type(kinds_str: str) -> ActivityType:
    """
    Works for:
    - OpenTripMap kinds: "view_points", "natural", "catering", ...
    - Geoapify categories: "tourism.sights", "catering.restaurant", ...
    - Google types: "tourist_attraction", "restaurant", "park", "viewpoint", ...
    """
    k = (kinds_str or "").lower()

    # Viewpoints
    if any(x in k for x in ["view_points", "viewpoint", "observation_tower", "lookout", "scenic"]):
        return "viewpoint"

    # Food
    if any(
        x in k
        for x in [
            "catering",
            "restaurant",
            "restaurants",
            "foods",
            "cafe",
            "fast_food",
            "food_court",
            "bakery",
            "bar",
        ]
    ):
        return "restaurant"

    # Nature / hiking
    if any(
        x in k
        for x in [
            "natural",
            "park",
            "leisure.park",
            "national_park",
            "protected_area",
            "forest",
            "trail",
            "hiking_area",
            "natural_feature",
            "campground",
        ]
    ):
        return "hiking"

    return "attraction"


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


def _rating_sort_key(it: dict[str, Any]) -> tuple[float, int, float]:
    """
    Prefer:
    - higher rating
    - more ratings
    - closer distance (tie-break)
    """
    rating = float(it.get("rating") or 0.0)
    rating_count = int(it.get("user_ratings_total") or 0)
    dist = float(it.get("distance") or 1e12)
    return (-rating, -rating_count, dist)


def _interest_target_types(interest: str) -> set[ActivityType]:
    i = (interest or "").strip().lower()
    if i in {"food"}:
        return {"restaurant"}
    if i in {"views", "view", "viewpoints"}:
        return {"viewpoint", "hiking"}
    if i in {"hiking", "nature"}:
        return {"hiking", "viewpoint"}
    if i in {"culture", "history", "museum", "museums"}:
        return {"attraction"}
    return set()


@router.post("/plan_trip", response_model=PlanResponse)
async def plan_trip(req: TripRequest) -> PlanResponse:
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

    providers = build_providers_chain()
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    provider_used: str | None = None

    for provider in providers:
        try:
            raw = await provider.search_radius(
                lon=lon,
                lat=lat,
                radius_m=DEFAULT_SEARCH_RADIUS_M,
                kinds=kinds,
                limit=30,
            )
            if raw:
                provider_used = provider.__class__.__name__
                break
        except Exception as e:
            errors.append(f"{provider.__class__.__name__}: {e}")
        if not PLACES_FALLBACK:
            break

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

    # Sort: distance first, then Google quality signals if present
    cleaned.sort(key=_sort_key)

    # Select "top rated per user interest" before turning candidates into activities.
    # This makes results more stable and avoids one category (e.g. restaurants) dominating.
    max_stops = 6
    interests = [s.strip().lower() for s in (req.interests or []) if s and s.strip()]
    unique_interests: list[str] = []
    for s in interests:
        if s not in unique_interests:
            unique_interests.append(s)
    interest_targets = {i: _interest_target_types(i) for i in unique_interests}

    # Precompute inferred type once per item.
    for it in cleaned:
        it["_inferred_type"] = infer_activity_type(it.get("kinds") or "")

    per_interest_quota = 0
    if unique_interests:
        per_interest_quota = max(1, max_stops // len(unique_interests))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    selected_counts: dict[str, int] = {}
    for interest in unique_interests:
        targets = interest_targets.get(interest) or set()
        if not targets:
            continue
        bucket = [it for it in cleaned if it.get("_inferred_type") in targets]
        bucket.sort(key=_rating_sort_key)
        take = bucket[:per_interest_quota]
        kept = 0
        for it in take:
            pid = str(it.get("xid") or "")
            if pid and pid in selected_ids:
                continue
            if pid:
                selected_ids.add(pid)
            selected.append(it)
            kept += 1
        selected_counts[interest] = kept

    # Fill remaining slots with best overall by rating (but keep variety already chosen).
    if len(selected) < max_stops:
        remainder = list(cleaned)
        remainder.sort(key=_rating_sort_key)
        for it in remainder:
            if len(selected) >= max_stops:
                break
            pid = str(it.get("xid") or "")
            if pid and pid in selected_ids:
                continue
            if pid:
                selected_ids.add(pid)
            selected.append(it)

    # Final ordering: closer first (more realistic day flow for MVP).
    selected.sort(key=_sort_key)

    activities: list[Activity] = []
    for item in selected:
        xid = item.get("xid")
        point = item.get("point") or {}
        name = item.get("name") or "Unnamed place"
        kinds_str = item.get("kinds") or ""

        a_type: ActivityType = infer_activity_type(kinds_str)

        # Optional: store rating in notes (helps you debug quality quickly)
        rating = item.get("rating")
        rating_count = item.get("user_ratings_total")
        notes_parts: list[str] = []

        if rating is not None:
            if rating_count is not None:
                notes_parts.append(f"rating {rating} ({rating_count})")
            else:
                notes_parts.append(f"rating {rating}")

        # naive source label for debugging
        if "user_ratings_total" in item or "rating" in item:
            notes_parts.append("src google")
        elif any("." in c for c in (item.get("categories") or [])):
            notes_parts.append("src geoapify")
        else:
            notes_parts.append("src otm")

        notes = " | ".join(notes_parts) if notes_parts else None

        activities.append(
            Activity(
                type=a_type,
                name=name,
                duration_minutes=default_visit_duration(kinds_str),
                location=[float(point["lon"]), float(point["lat"])],
                source_id=xid if xid else None,
                notes=notes,
            )
        )
        if len(activities) >= 6:
            break

    total = 0
    kept: list[Activity] = []
    for a in activities:
        if total + a.duration_minutes > 7 * 60:
            break
        kept.append(a)
        total += a.duration_minutes

    if not kept:
        day = DayPlan(day=1, total_duration_hours=0, activities=[])
        note = "No results found in area."
        if errors:
            note += f" Errors: {' | '.join(errors)}"
        return PlanResponse(
            query={
                "days": req.days,
                "pace": req.pace,
                "interests": req.interests,
                "travel_mode": req.travel_mode,
                "geometry_type": req.geometry.get("type"),
                "radius_m": DEFAULT_SEARCH_RADIUS_M,
                "provider": provider_used,
                "raw_count": len(raw),
                "cleaned_count": len(cleaned),
                "selected_counts": selected_counts,
                "note": note,
            },
            itinerary=[day],
            status="ok",
        )

    day = DayPlan(day=1, total_duration_hours=round(total / 60.0, 1), activities=kept)

    return PlanResponse(
        query={
            "days": req.days,
            "pace": req.pace,
            "interests": req.interests,
            "travel_mode": req.travel_mode,
            "geometry_type": req.geometry.get("type"),
            "radius_m": DEFAULT_SEARCH_RADIUS_M,
            "provider": provider_used,
            "raw_count": len(raw),
            "cleaned_count": len(cleaned),
            "selected_counts": selected_counts,
        },
        itinerary=[day],
        status="ok",
    )
