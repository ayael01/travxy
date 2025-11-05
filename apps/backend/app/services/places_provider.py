from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlacesProvider(Protocol):
    async def search_radius(
        self, *, lon: float, lat: float, radius_m: int, kinds: str | None, limit: int
    ) -> list[dict[str, Any]]:
        """Return a list of place dicts with keys: name (str), kinds (str), point {'lon','lat'}, xid (str|None)."""
        ...

    async def place_details(self, xid: str) -> dict[str, Any]:
        """Return a full detail dict for a place id when supported, else {}."""
        ...
