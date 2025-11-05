import logging
from typing import Any

import httpx
from app.core.config import (
    DEBUG,
    DEFAULT_SEARCH_RADIUS_M,
    OPENTRIPMAP_API_BASE,
    OPENTRIPMAP_API_KEY,
    OPENTRIPMAP_LANG,
)

# Logger
logger = logging.getLogger("travxy.opentripmap")
if DEBUG:
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        h = logging.StreamHandler()
        fmt = logging.Formatter("[OTM] %(levelname)s: %(message)s")
        h.setFormatter(fmt)
        logger.addHandler(h)

# Keep a conservative mapping
INTERESTS_TO_KINDS = {
    "hiking": ["natural", "view_points"],
    "views": ["view_points", "natural"],
    "food": ["catering"],  # "restaurants" is not a kind
    "culture": ["cultural", "museums", "architecture", "historic"],
}

ALLOWED_KINDS = {
    "natural",
    "view_points",
    "catering",
    "cultural",
    "museums",
    "architecture",
    "historic",
}

SAFE_DEFAULT_KINDS = "natural,view_points,catering,cultural,museums,architecture"


def compose_kinds(interests: list[str]) -> str | None:
    kinds: list[str] = []
    for interest in interests:
        kinds.extend(INTERESTS_TO_KINDS.get(interest, []))
    safe = sorted({k for k in kinds if k in ALLOWED_KINDS})
    return ",".join(safe) if safe else None


def _mask_key(s: str) -> str:
    if not s:
        return s
    return s[:6] + "…" + s[-4:]


def polygon_centroid(geom: dict[str, Any]) -> tuple[float, float]:
    coords = geom.get("coordinates", [[]])[0]
    if not coords:
        raise ValueError("Invalid Polygon coordinates")
    lon_sum = sum(pt[0] for pt in coords)
    lat_sum = sum(pt[1] for pt in coords)
    n = len(coords)
    return (lon_sum / n, lat_sum / n)


def extract_origin(geometry: dict[str, Any]) -> tuple[float, float]:
    gtype = geometry.get("type")
    if gtype == "Point":
        lon, lat = geometry["coordinates"]
        return float(lon), float(lat)
    if gtype in {"Polygon", "MultiPolygon"}:
        return polygon_centroid(geometry)
    raise ValueError(f"Unsupported geometry type for MVP: {gtype}")


async def _places_radius(
    lon: float, lat: float, radius_m: int, kinds: str | None, limit: int, include_rate: bool
) -> list[dict[str, Any]]:
    url = f"{OPENTRIPMAP_API_BASE}/{OPENTRIPMAP_LANG}/places/radius"
    params: dict[str, Any] = {
        "apikey": OPENTRIPMAP_API_KEY,
        "lon": lon,
        "lat": lat,
        "radius": radius_m,
        "limit": limit,
        "format": "json",
    }
    if kinds:
        params["kinds"] = kinds
    if include_rate:
        params["rate"] = 2

    if DEBUG:
        log_params = {**params, "apikey": _mask_key(str(params.get("apikey", "")))}
        logger.debug(f"REQUEST  GET {url}")
        logger.debug(f"         params={log_params}")

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        text = r.text

        if DEBUG:
            logger.debug(f"RESPONSE {r.status_code} ({len(text)} bytes)")
            snippet = text[:400].replace("\n", " ")
            logger.debug(f"         body[:400]={snippet}")

        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"{e.response.status_code} - {e.response.text}") from e

        data = r.json()
        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(f"provider-error: {data['error']}")
            return []
        if isinstance(data, list):
            return data
        return []


async def fetch_places_radius(
    lon: float, lat: float, radius_m: int, kinds: str | None, limit: int = 30
) -> list[dict[str, Any]]:
    first_kinds = kinds or SAFE_DEFAULT_KINDS

    try:
        first = await _places_radius(lon, lat, radius_m, first_kinds, limit, include_rate=True)
        if first:
            return first
    except RuntimeError as e:
        if "Unknown category name" not in str(e):
            raise
        if DEBUG:
            logger.debug(f"FALLBACK reason={e}")

    for r in [
        radius_m,
        int(radius_m * 1.5),
        int(radius_m * 2.0),
        max(DEFAULT_SEARCH_RADIUS_M, 20000),
    ]:
        res = await _places_radius(lon, lat, r, None, limit, include_rate=False)
        if res:
            return res
    return []


async def fetch_place_details(xid: str) -> dict[str, Any]:
    url = f"{OPENTRIPMAP_API_BASE}/{OPENTRIPMAP_LANG}/places/xid/{xid}"
    params = {"apikey": OPENTRIPMAP_API_KEY}
    if DEBUG:
        logger.debug(f"REQUEST  GET {url}  apikey={_mask_key(OPENTRIPMAP_API_KEY)}")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        if DEBUG:
            logger.debug(f"RESPONSE {r.status_code} len={len(r.text)}")
        r.raise_for_status()
        return r.json()
