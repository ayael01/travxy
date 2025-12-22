from __future__ import annotations

import asyncio
import json
import math
import os
import re
from typing import Any, cast

import httpx
from app.schemas.candidates import Candidate
from app.schemas.itinerary import DayPlan, ItineraryBuildRequest, ItineraryResponse, PlannedActivity
from app.services.opentripmap import extract_origin
from app.utils.duration import default_visit_duration


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _estimate_drive_minutes(distance_m: float) -> int:
    km = max(distance_m, 0.0) / 1000.0
    moving = (km / 45.0) * 60.0
    parking = 5.0
    return int(round(max(5.0, moving + parking)))


def _score(c: Candidate) -> float:
    rating = float(c.rating or 0.0)
    count = float(c.user_ratings_total or 0)
    dist = float(c.distance_m or 1e12)
    return rating * 1000.0 + math.log10(count + 1.0) * 50.0 - dist / 1000.0


def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\\s*```$", "", text)
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}")
        text = text[start : end + 1]
    return json.loads(text)


async def _openai_chat_json(
    *,
    system: str,
    user: dict[str, Any],
    model: str,
    temperature: float,
    base_url: str,
    api_key: str,
    timeout_s: int,
) -> Any:
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
    base_delay_ms = int(os.getenv("OPENAI_RETRY_BASE_MS", "600"))
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return _extract_json(content)
            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                # Retry on rate limits and transient gateway errors.
                if status in {429, 500, 502, 503, 504} and attempt < max_retries:
                    retry_after = e.response.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay_s = float(retry_after)
                        except ValueError:
                            delay_s = (base_delay_ms / 1000.0) * (2**attempt)
                    else:
                        delay_s = (base_delay_ms / 1000.0) * (2**attempt)
                    await asyncio.sleep(delay_s)
                    continue
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                if attempt < max_retries:
                    delay_s = (base_delay_ms / 1000.0) * (2**attempt)
                    await asyncio.sleep(delay_s)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("OpenAI request failed unexpectedly")


def _stable_shuffle(items: list[Candidate], *, seed: int) -> list[Candidate]:
    keyed: list[tuple[int, Candidate]] = []
    for c in items:
        k = f"{c.xid or ''}|{c.name}|{c.inferred_type}|{c.distance_m or ''}"
        h = (seed * 1315423911) ^ (hash(k) & 0xFFFFFFFF)
        keyed.append((h, c))
    keyed.sort(key=lambda t: t[0])
    return [c for _, c in keyed]


def _prepare_candidate_payload(req: ItineraryBuildRequest, *, seed: int) -> list[dict[str, Any]]:
    hard_cap = int(os.getenv("LLM_CANDIDATE_HARD_CAP", "600"))
    per_type_cap = int(os.getenv("LLM_CANDIDATE_PER_TYPE_CAP", "150"))

    locked = {x for x in req.locked_xids if x}
    excluded = {x for x in req.excluded_xids if x}
    by_id = {c.xid: c for c in req.candidates if c.xid}

    locked_candidates = [by_id[x] for x in locked if x in by_id and x not in excluded]
    eligible = [c for c in req.candidates if c.xid and c.xid not in excluded]
    eligible.sort(key=_score, reverse=True)

    top_overall = eligible[: min(len(eligible), hard_cap)]

    per_type: list[Candidate] = []
    for t in ["hiking", "viewpoint", "attraction", "restaurant", "lodging"]:
        pool = [c for c in eligible if c.inferred_type == t]
        per_type.extend(pool[: min(len(pool), per_type_cap)])

    merged: dict[str, Candidate] = {}
    for c in locked_candidates + top_overall + per_type:
        if not c.xid:
            continue
        merged[c.xid] = c

    selected = _stable_shuffle(list(merged.values()), seed=seed)[:hard_cap]
    return [
        {
            "xid": c.xid,
            "name": c.name,
            "type": c.inferred_type,
            "rating": c.rating,
            "user_ratings_total": c.user_ratings_total,
            "distance_m": c.distance_m,
            "location": c.location,
            "kinds": c.kinds,
            "categories": c.categories[:8] if c.categories else [],
            "provider": c.provider,
        }
        for c in selected
        if c.xid
    ]


async def _llm_shortlist(
    req: ItineraryBuildRequest,
    *,
    seed: int,
    model: str,
    temperature: float,
    base_url: str,
    api_key: str,
) -> tuple[list[str], dict[str, Any]]:
    payload = _prepare_candidate_payload(req, seed=seed)
    max_keep = int(os.getenv("LLM_SHORTLIST_SIZE", "200"))

    system = (
        "You are a travel planning assistant.\n"
        "Task: choose a SHORTLIST of candidates to consider for a 1-day itinerary.\n"
        "Return ONLY JSON.\n"
        "Rules:\n"
        f"- Return at most {max_keep} xids.\n"
        "- Use only xids present in candidates.\n"
        "- Include all locked_xids.\n"
        "- Exclude excluded_xids.\n"
        "- Ensure diversity across the user's interests.\n"
    )

    user = {
        "trip": req.trip.model_dump(),
        "locked_xids": req.locked_xids,
        "excluded_xids": req.excluded_xids,
        "variant": req.variant,
        "seed": seed,
        "max_keep": max_keep,
        "candidates": payload,
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "shortlist_xids": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["shortlist_xids"],
        },
    }

    parsed = await _openai_chat_json(
        system=system,
        user=user,
        model=model,
        temperature=temperature,
        base_url=base_url,
        api_key=api_key,
        timeout_s=90,
    )
    xs = parsed.get("shortlist_xids") or []
    if not isinstance(xs, list):
        raise ValueError("LLM shortlist missing shortlist_xids list")
    out = [str(x) for x in xs if x]
    diag = {"shortlist_size": len(out), "llm_notes": parsed.get("notes")}
    return out[:max_keep], diag


async def _llm_build(
    req: ItineraryBuildRequest,
    *,
    seed: int,
    model: str,
    temperature: float,
    base_url: str,
    api_key: str,
    candidates_payload: list[dict[str, Any]],
    attempt: int,
    fix_message: str | None = None,
) -> dict[str, Any]:
    system = (
        "You are a senior vacation travel agent. You create a polished day-trip plan.\n"
        "Return ONLY JSON.\n"
        "Hard rules:\n"
        "- Use ONLY candidates provided (by xid). Never invent places.\n"
        "- Must include all locked_xids.\n"
        "- Must exclude excluded_xids.\n"
        "- Build a coherent day with times (HH:MM), minimal backtracking, and realistic pacing.\n"
        "- Provide nice titles and friendly descriptions for each stop.\n"
        "- Output must match the given output_schema exactly.\n"
    )

    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "day": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "overview": {"type": "string"},
                    "tips": {"type": "array", "items": {"type": "string"}},
                    "activities": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "xid": {"type": "string"},
                                "start_time": {"type": "string"},
                                "end_time": {"type": "string"},
                                "segment": {"type": "string"},
                                "description": {"type": "string"},
                                "why_this": {"type": "string"},
                            },
                            "required": ["xid", "start_time", "end_time", "description"],
                        },
                    },
                },
                "required": ["activities"],
            },
        },
        "required": ["title", "summary", "day"],
    }

    user: dict[str, Any] = {
        "trip": req.trip.model_dump(),
        "locked_xids": req.locked_xids,
        "excluded_xids": req.excluded_xids,
        "previous_itineraries": req.previous_itineraries,
        "variant": req.variant,
        "seed": seed,
        "attempt": attempt,
        "constraints": {
            "start_time": req.start_time,
            "max_total_minutes": req.max_total_minutes,
            "max_stops": req.max_stops,
        },
        "candidates": candidates_payload,
        "output_schema": output_schema,
    }
    if fix_message:
        user["fix_message"] = fix_message

    parsed = await _openai_chat_json(
        system=system,
        user=user,
        model=model,
        temperature=temperature,
        base_url=base_url,
        api_key=api_key,
        timeout_s=120,
    )
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not an object")
    return cast(dict[str, Any], parsed)


async def build_itinerary(req: ItineraryBuildRequest) -> ItineraryResponse:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (LLM stage required)")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.8"))

    base_seed = int(os.getenv("ITINERARY_SEED", "1337"))
    seed = base_seed + int(req.variant or 0)
    diagnostics: dict[str, Any] = {"seed": seed, "model": model, "temperature": temperature}

    shortlist_threshold = int(os.getenv("LLM_SHORTLIST_THRESHOLD", "250"))
    candidates_for_prompt = req.candidates
    if len(req.candidates) > shortlist_threshold:
        shortlist_xids, shortlist_diag = await _llm_shortlist(
            req, seed=seed, model=model, temperature=temperature, base_url=base_url, api_key=api_key
        )
        diagnostics["shortlist"] = shortlist_diag
        by_id = {c.xid: c for c in req.candidates if c.xid}
        candidates_for_prompt = [by_id[x] for x in shortlist_xids if x in by_id]

    tmp_req = ItineraryBuildRequest(**{**req.model_dump(), "candidates": candidates_for_prompt})
    candidates_payload = _prepare_candidate_payload(tmp_req, seed=seed)

    parsed = await _llm_build(
        tmp_req,
        seed=seed,
        model=model,
        temperature=temperature,
        base_url=base_url,
        api_key=api_key,
        candidates_payload=candidates_payload,
        attempt=1,
    )

    by_id_all = {c.xid: c for c in tmp_req.candidates if c.xid}
    excluded = {x for x in tmp_req.excluded_xids if x}
    locked = [x for x in tmp_req.locked_xids if x]
    day_obj = cast(dict[str, Any], parsed.get("day") or {})
    activities_in_any = day_obj.get("activities")
    activities_in: list[dict[str, Any]] = (
        cast(list[dict[str, Any]], activities_in_any) if isinstance(activities_in_any, list) else []
    )

    chosen_xids = [str(a.get("xid")) for a in activities_in if isinstance(a, dict) and a.get("xid")]
    invalid = [x for x in chosen_xids if x not in by_id_all or x in excluded]
    missing_locked = [
        x for x in locked if x not in chosen_xids and x in by_id_all and x not in excluded
    ]
    if invalid or missing_locked:
        fix_msg = {
            "invalid_xids": invalid,
            "missing_locked_xids": missing_locked,
            "excluded_xids": sorted(excluded),
        }
        parsed = await _llm_build(
            tmp_req,
            seed=seed,
            model=model,
            temperature=temperature,
            base_url=base_url,
            api_key=api_key,
            candidates_payload=candidates_payload,
            attempt=2,
            fix_message=json.dumps(fix_msg),
        )
        day_obj = cast(dict[str, Any], parsed.get("day") or {})
        activities_in_any = day_obj.get("activities")
        activities_in = (
            cast(list[dict[str, Any]], activities_in_any)
            if isinstance(activities_in_any, list)
            else []
        )

    title = cast(str | None, parsed.get("title"))
    summary = cast(str | None, parsed.get("summary"))

    origin_lon, origin_lat = extract_origin(req.trip.geometry)

    activities_out: list[PlannedActivity] = []
    for a in activities_in:
        if not isinstance(a, dict):
            continue
        xid = str(a.get("xid") or "")
        if not xid or xid in excluded:
            continue
        c = by_id_all.get(xid)
        if not c:
            continue
        dur = default_visit_duration(c.kinds or "")
        activities_out.append(
            PlannedActivity(
                xid=c.xid,
                type=c.inferred_type,
                name=c.name,
                duration_minutes=int(dur),
                location=list(c.location),
                provider=c.provider,
                description=str(a.get("description") or "") or None,
                why_this=str(a.get("why_this") or "") or None,
                start_time=str(a.get("start_time") or "") or None,
                end_time=str(a.get("end_time") or "") or None,
                segment=str(a.get("segment") or "") or None,
            )
        )

    if not activities_out:
        raise ValueError("LLM did not return any valid activities")

    # Add drive estimate for display (backend still enforces "use only known places").
    prev_lon, prev_lat = origin_lon, origin_lat
    for a in activities_out:
        dist_m = _haversine_m(prev_lon, prev_lat, a.location[0], a.location[1])
        a.travel_minutes_from_prev = _estimate_drive_minutes(dist_m)
        prev_lon, prev_lat = a.location[0], a.location[1]

    day_title = cast(str | None, day_obj.get("title"))
    day_overview = cast(str | None, day_obj.get("overview"))
    tips_any = day_obj.get("tips")
    tips_list: list[Any] = cast(list[Any], tips_any) if isinstance(tips_any, list) else []
    tips = [str(t) for t in tips_list if t]

    day = DayPlan(
        day=1,
        title=day_title,
        overview=day_overview,
        tips=tips,
        total_duration_hours=0.0,
        activities=activities_out,
    )

    return ItineraryResponse(
        query={"mode": "llm", "diagnostics": diagnostics},
        title=title,
        summary=summary,
        itinerary=[day],
        status="ok",
    )
