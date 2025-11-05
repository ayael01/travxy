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


class Activity(BaseModel):
    type: Literal["hiking", "restaurant", "attraction", "viewpoint", "lodging"]
    name: str
    duration_minutes: int
    location: list[float]  # [lon, lat]
    source_id: str | None = None
    notes: str | None = None


class DayPlan(BaseModel):
    day: int
    total_duration_hours: float
    activities: list[Activity]


class PlanResponse(BaseModel):
    query: dict[str, Any]
    itinerary: list[DayPlan]
    status: Literal["ok", "error"] = "ok"
