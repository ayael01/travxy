from typing import Any, Literal

from app.schemas.candidates import Candidate, CandidateType
from app.schemas.trip import TripRequest
from pydantic import BaseModel, Field


class PlannedActivity(BaseModel):
    xid: str | None = None
    type: CandidateType
    name: str
    duration_minutes: int
    location: list[float]  # [lon, lat]
    provider: str | None = None
    description: str | None = None
    why_this: str | None = None
    notes: str | None = None
    start_time: str | None = None  # "HH:MM"
    end_time: str | None = None  # "HH:MM"
    travel_minutes_from_prev: int | None = None
    segment: str | None = None  # "Morning" | "Lunch" | "Afternoon" | ...


class DayPlan(BaseModel):
    day: int
    title: str | None = None
    overview: str | None = None
    tips: list[str] = Field(default_factory=list)
    total_duration_hours: float
    activities: list[PlannedActivity]


class ItineraryBuildRequest(BaseModel):
    trip: TripRequest
    candidates: list[Candidate]
    locked_xids: list[str] = Field(default_factory=list)
    excluded_xids: list[str] = Field(default_factory=list)
    previous_itineraries: list[list[str]] = Field(default_factory=list)
    variant: int = 0
    max_stops: int = 6
    start_time: str = "09:30"
    max_total_minutes: int = 7 * 60


class ItineraryResponse(BaseModel):
    query: dict[str, Any]
    title: str | None = None
    summary: str | None = None
    itinerary: list[DayPlan]
    status: Literal["ok", "error"] = "ok"
