import logging
import math
from typing import Any, Literal

from app.core.config import (
    DEFAULT_SEARCH_RADIUS_M,
    GEOAPIFY_API_KEY,
    OPENTRIPMAP_API_KEY,
    PLACES_FALLBACK,
)
from app.schemas.trip import Activity, DayPlan, PlanResponse, TripRequest
from app.services.opentripmap import compose_kinds, extract_origin  # keep our helpers
from app.services.providers_factory import build_providers_chain
from app.utils.duration import default_visit_duration
from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger("travxy.router.plan")

ActivityType = Literal["hiking", "restaurant", "attraction", "viewpoint", "lodging"]

# Accept only POIs from these category prefixes (works for Geoapify)
_ALLOWED_CATEGORY_PREFIXES = (
    "tourism.attraction",
    "tourism.sights",
    "catering",
)

# Drop roads/addresses outright
_BLOCKED_CATEGORY_PREFIXES = (
    "highway.",
    "address.",
)


def infer_activity_type(kinds_str: str) -> ActivityType:
    k = (kinds_str or "").lower()
    if ("view_points" in k) or ("viewpoint" in k) or ("observation_tower" in k):
        return "viewpoint"
    if any(
        x in k
        for x in [
            "catering",
            "restaurants",
            "foods",
            "restaurant",
            "cafe",
            "fast_food",
            "food_court",
        ]
    ):
        return "restaurant"
    if ("natural" in k) or any(
        x in k
        for x in ["national_park", "forest", "protected_area", "trail", "park", "leisure.park"]
    ):
        return "hiking"
    return "attraction"


# Basic noise filters for city centers (avoid plaques / address points etc.)
def _is_noise(kinds_str: str, name: str) -> bool:
    k = (kinds_str or "").lower()
    nm = (name or "").lower().strip()
    if any(bad in k for bad in ["memorial", "plaque", "address_point"]):
        return True
    # skip very generic, address-like names if we have better options
    if len(nm) <= 3:
        return True
    return False


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    # distance in meters
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@router.post("/plan_trip", response_model=PlanResponse)
async def plan_trip(req: TripRequest) -> PlanResponse:
    if not (GEOAPIFY_API_KEY or OPENTRIPMAP_API_KEY):
        raise HTTPException(
            status_code=500,
            detail="No places provider configured. Please set GEOAPIFY_API_KEY or OPENTRIPMAP_API_KEY",
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
                break
        except Exception as e:
            errors.append(f"{provider.__class__.__name__}: {e}")
        if not PLACES_FALLBACK:
            break

    # --- normalize + filter + sort ---
    cleaned: list[dict[str, Any]] = []
    seen_names_coords: set[tuple[str, float, float]] = set()

    for item in raw:
        point = item.get("point") or {}
        if "lon" not in point or "lat" not in point:
            continue

        name = (item.get("name") or "Unnamed place").strip()
        kinds_str = item.get("kinds") or ""

        # categories from Geoapify come as CSV or list — normalize to list of strings
        cats_raw = item.get("categories") or item.get("kinds") or ""
        if isinstance(cats_raw, str):
            cats = [c.strip() for c in cats_raw.split(",") if c.strip()]
        elif isinstance(cats_raw, list):
            cats = [str(c).strip() for c in cats_raw if str(c).strip()]
        else:
            cats = []

        # Block by categories we don't want (roads/addresses)
        if any(c.startswith(_BLOCKED_CATEGORY_PREFIXES) for c in cats):
            continue

        # Keep only if at least one desired prefix is present
        if cats and not any(c.startswith(_ALLOWED_CATEGORY_PREFIXES) for c in cats):
            continue

        # Name-based noise guard (very short or obvious street-like)
        if _is_noise(kinds_str, name):
            continue
        if name.lower().startswith(("rue ", "avenue ", "boulevard ", "place ")):
            continue

        # De-dupe by name & proximity
        lon2 = float(point["lon"])
        lat2 = float(point["lat"])
        key_name = name.lower()

        # exact sig check first (cheap)
        sig = (key_name, round(lon2, 6), round(lat2, 6))
        if sig in seen_names_coords:
            continue

        # proximity check: same name within 75 m -> skip
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

    # prefer nearer items (if Geoapify provided distance), then by name
    cleaned.sort(
        key=lambda it: (
            it.get("distance") or 1e12,
            len(it.get("name") or ""),
        )
    )

    activities: list[Activity] = []
    for item in cleaned:
        xid = item.get("xid")
        point = item.get("point") or {}
        name = item.get("name") or "Unnamed place"
        kinds_str = item.get("kinds") or ""
        a_type: ActivityType = infer_activity_type(kinds_str)

        activities.append(
            Activity(
                type=a_type,
                name=name,
                duration_minutes=default_visit_duration(kinds_str),
                location=[float(point["lon"]), float(point["lat"])],
                source_id=xid if xid else None,
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
        },
        itinerary=[day],
        status="ok",
    )
