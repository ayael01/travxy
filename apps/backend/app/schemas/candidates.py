from typing import Any, Literal

from pydantic import BaseModel

CandidateType = Literal["hiking", "restaurant", "attraction", "viewpoint", "lodging"]


class Candidate(BaseModel):
    xid: str | None = None
    name: str
    location: list[float]  # [lon, lat]
    distance_m: float | None = None
    inferred_type: CandidateType
    kinds: str | None = None
    categories: list[str] = []
    rating: float | None = None
    user_ratings_total: int | None = None
    provider: str | None = None


class CandidatesResponse(BaseModel):
    query: dict[str, Any]
    candidates: list[Candidate]
    status: Literal["ok", "error"] = "ok"
