def default_visit_duration(kind: str) -> int:
    k = (kind or "").lower()
    if any(x in k for x in ["architecture", "historic", "cultural", "museum"]):
        return 90
    if any(x in k for x in ["natural", "view_points", "geological"]):
        return 25
    if any(x in k for x in ["foods", "restaurants", "catering", "restaurant", "cafe"]):
        return 90
    return 45
