def default_visit_duration(kind: str) -> int:
    """
    Heuristics that work for:
    - OTM kinds: natural, view_points, catering, museums, ...
    - Geoapify categories: tourism.sights, catering.restaurant, ...
    - Google types: tourist_attraction, point_of_interest, park, restaurant, museum, ...
    """
    k = (kind or "").lower()

    # Food
    if any(
        x in k
        for x in ["restaurant", "catering", "foods", "cafe", "fast_food", "food_court", "bakery"]
    ):
        return 75

    # Museums / culture
    if any(
        x in k
        for x in [
            "museum",
            "museums",
            "art_gallery",
            "cultural",
            "historic",
            "architecture",
            "church",
            "synagogue",
            "mosque",
            "place_of_worship",
        ]
    ):
        return 90

    # Big nature / hiking
    if any(
        x in k
        for x in ["hiking_area", "trail", "national_park", "protected_area", "forest", "campground"]
    ):
        return 120

    # Parks / nature light
    if any(x in k for x in ["park", "natural", "natural_feature", "leisure.park"]):
        return 60

    # Viewpoints are usually quick stops
    if any(x in k for x in ["view_points", "viewpoint", "observation_tower", "lookout", "scenic"]):
        return 25

    # Generic POI / attraction
    if any(
        x in k
        for x in ["tourist_attraction", "point_of_interest", "tourism.attraction", "tourism.sights"]
    ):
        return 60

    return 45
