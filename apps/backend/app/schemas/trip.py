from typing import Any, Literal

from pydantic import BaseModel, Field


class TripRequest(BaseModel):
    geometry: dict[str, Any] = Field(..., description="GeoJSON Point or Polygon")
    days: int = 1
    pace: Literal["easy", "moderate", "intense"] = "moderate"
    interests: list[str] = ["hiking", "views", "food"]
    travel_mode: Literal["car", "foot", "bike"] = "car"
    budget: Literal["basic", "mid", "luxury"] | None = None
    date: str | None = None
