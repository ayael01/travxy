from app.core.config import PLACES_PRIMARY
from app.services.places_provider import PlacesProvider
from app.services.providers_geoapify import GeoapifyProvider
from app.services.providers_opentripmap import OpenTripMapProvider


def build_providers_chain() -> list[PlacesProvider]:
    primary = (PLACES_PRIMARY or "geoapify").lower()
    if primary == "opentripmap":
        return [OpenTripMapProvider(), GeoapifyProvider()]
    # default: geoapify first
    return [GeoapifyProvider(), OpenTripMapProvider()]
