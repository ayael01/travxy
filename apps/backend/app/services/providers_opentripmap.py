from typing import Any

from app.services.opentripmap import fetch_place_details, fetch_places_radius


class OpenTripMapProvider:
    async def search_radius(
        self, *, lon: float, lat: float, radius_m: int, kinds: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return await fetch_places_radius(lon, lat, radius_m, kinds, limit=limit)

    async def place_details(self, xid: str) -> dict[str, Any]:
        if not xid:
            return {}
        return await fetch_place_details(xid)
