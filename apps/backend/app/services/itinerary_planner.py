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


def _parse_hhmm(s: str) -> int:
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        raise ValueError(f"Invalid time format (expected HH:MM): {s!r}")
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError(f"Invalid time value: {s!r}")
    return hh * 60 + mm


def _format_hhmm(minutes: int) -> str:
    minutes = int(minutes) % (24 * 60)
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}"


def _build_day_slots(req: ItineraryBuildRequest) -> list[dict[str, Any]]:
    """
    Build an ordered list of "slots" (a day skeleton) based on interests and time budget.
    The LLM fills these slots with candidates (xids) and must respect required slots.
    """
    raw_interests = [str(x).strip().lower() for x in (req.trip.interests or []) if str(x).strip()]
    aliases = {
        # common typos / synonyms from UI
        "view": "views",
        "viewpoint": "views",
        "view_points": "views",
        "scenic": "views",
        "restaurant": "food",
        "restaurants": "food",
        "breakfast": "food",
        "brunch": "food",
        "cafe": "food",
        "cafes": "food",
        "nightlife": "food",
        "bars": "food",
        "bar": "food",
    }
    interests = [aliases.get(x, x) for x in raw_interests]
    interest_set = set(interests)

    start_min = _parse_hhmm(req.start_time)
    end_min = start_min + int(req.max_total_minutes or 0)
    total_min = max(0, end_min - start_min)

    include_food = "food" in interest_set
    include_hiking = "hiking" in interest_set
    include_views = "views" in interest_set
    include_culture = "culture" in interest_set

    available_types = {c.inferred_type for c in (req.candidates or []) if c.inferred_type}

    def add_slot(
        *,
        key: str,
        label: str,
        required: bool,
        allowed_types: list[str],
        suggested_duration_min: int,
        suggested_duration_max: int,
        preferred_start_min: int | None = None,
        preferred_end_min: int | None = None,
    ) -> dict[str, Any]:
        allowed_types = list(dict.fromkeys([t for t in allowed_types if t]))
        return {
            "key": key,
            "label": label,
            "required": required,
            "allowed_types": allowed_types,
            "suggested_duration_min": suggested_duration_min,
            "suggested_duration_max": suggested_duration_max,
            "preferred_start_time": _format_hhmm(preferred_start_min)
            if preferred_start_min
            else None,
            "preferred_end_time": _format_hhmm(preferred_end_min) if preferred_end_min else None,
        }

    slots: list[dict[str, Any]] = []

    # Decide a primary non-food activity type.
    if include_hiking:
        primary_type = "hiking"
    elif include_views:
        primary_type = "viewpoint"
    elif include_culture:
        primary_type = "attraction"
    elif include_food:
        primary_type = "restaurant"
    else:
        primary_type = "attraction"

    # If a preferred type doesn't exist in candidates, fall back gracefully.
    if primary_type not in available_types:
        for fallback in ["attraction", "hiking", "restaurant", "viewpoint"]:
            if fallback in available_types:
                primary_type = fallback
                break

    # If user wants ONLY food, treat the day as a food crawl.
    only_food = include_food and not (include_hiking or include_views or include_culture)

    # Helper: include meals only if there is enough time to make them sensible.
    has_morning = total_min >= 180
    has_midday = total_min >= 240
    has_evening = end_min >= _parse_hhmm("18:00") and total_min >= 420

    has_restaurants = "restaurant" in available_types

    if include_food and has_morning and has_restaurants:
        slots.append(
            add_slot(
                key="breakfast",
                label="Breakfast / coffee",
                required=only_food and total_min >= 240,
                allowed_types=["restaurant"],
                suggested_duration_min=30,
                suggested_duration_max=75,
                preferred_start_min=max(start_min, _parse_hhmm("08:30")),
                preferred_end_min=min(end_min, _parse_hhmm("10:45")),
            )
        )

    # Main experience block
    if primary_type == "hiking":
        dur_min, dur_max = 150, 240
        label = "Main hike"
    elif primary_type == "restaurant":
        dur_min, dur_max = 75, 105
        label = "Main food stop"
    elif primary_type == "viewpoint":
        dur_min, dur_max = 45, 90
        label = "Scenic highlight"
    else:
        dur_min, dur_max = 75, 135
        label = "Main attraction"

    slots.append(
        add_slot(
            key="main",
            label=label,
            required=True,
            allowed_types=[primary_type],
            suggested_duration_min=dur_min,
            suggested_duration_max=dur_max,
        )
    )

    if include_food and has_midday and has_restaurants:
        slots.append(
            add_slot(
                key="lunch",
                label="Lunch",
                required=include_food and (not only_food) and end_min >= _parse_hhmm("13:30"),
                allowed_types=["restaurant"],
                suggested_duration_min=60,
                suggested_duration_max=90,
                preferred_start_min=max(start_min, _parse_hhmm("11:45")),
                preferred_end_min=min(end_min, _parse_hhmm("15:00")),
            )
        )

    # Secondary block based on remaining interests.
    secondary_types: list[str] = []
    if include_views and primary_type != "viewpoint":
        # Our providers often classify scenic spots as "hiking" or "attraction" rather than "viewpoint".
        secondary_types.extend(["viewpoint", "hiking", "attraction"])
    if include_culture and primary_type != "attraction":
        secondary_types.append("attraction")
    if include_hiking and primary_type != "hiking":
        secondary_types.append("hiking")

    # If nothing else, allow a short scenic/attraction filler.
    if not secondary_types and not only_food:
        secondary_types = ["viewpoint", "attraction"]

    # Keep only types that exist; if empty, allow a broad fallback.
    secondary_types = [t for t in secondary_types if t in available_types]
    if not secondary_types and not only_food:
        secondary_types = [
            t for t in ["attraction", "hiking", "restaurant"] if t in available_types
        ]

    # Only add a secondary slot if we have time budget and stop budget.
    if secondary_types and total_min >= 330 and int(req.max_stops or 0) >= 3:
        slots.append(
            add_slot(
                key="afternoon",
                label="Afternoon",
                required=False,
                allowed_types=secondary_types,
                suggested_duration_min=45,
                suggested_duration_max=120,
            )
        )

    if include_food and has_evening and has_restaurants:
        slots.append(
            add_slot(
                key="dinner",
                label="Dinner",
                required=only_food and total_min >= 600,
                allowed_types=["restaurant"],
                suggested_duration_min=75,
                suggested_duration_max=105,
                preferred_start_min=max(start_min, _parse_hhmm("17:30")),
                preferred_end_min=min(end_min, _parse_hhmm("21:00")),
            )
        )

    # Cap slot count to max_stops (keep required ones).
    max_stops = int(req.max_stops or 0) or 6
    required_keys = {s["key"] for s in slots if s.get("required")}
    if len(slots) > max_stops:
        kept: list[dict[str, Any]] = []
        for s in slots:
            if s["key"] in required_keys:
                kept.append(s)
        # Fill remaining by order, skipping already kept.
        for s in slots:
            if len(kept) >= max_stops:
                break
            if s["key"] not in {k["key"] for k in kept}:
                kept.append(s)
        slots = kept[:max_stops]

    # Always keep order stable (main should never be dropped).
    return slots


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
    slots = _build_day_slots(req)
    slot_keys = [s["key"] for s in slots]
    slot_by_key = {s["key"]: s for s in slots}

    system = (
        "You are a senior vacation travel agent. You create a polished day-trip plan.\n"
        "Return ONLY JSON.\n"
        "Hard rules:\n"
        "- Use ONLY candidates provided (by xid). Never invent places.\n"
        "- Must include all locked_xids.\n"
        "- Must exclude excluded_xids.\n"
        "- Build a coherent day with times (HH:MM), minimal backtracking, and realistic pacing.\n"
        "- Follow the provided day slots (day skeleton) in order.\n"
        "- At most one activity per slot.\n"
        "- Only choose a candidate whose type matches the slot's allowed_types.\n"
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
                                "slot": {"type": "string", "enum": slot_keys},
                                "description": {"type": "string"},
                                "why_this": {"type": "string"},
                            },
                            "required": ["xid", "slot", "start_time", "end_time", "description"],
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
        "day_slots": slots,
        "slot_rules": {
            "slot_order": slot_keys,
            "required_slots": [k for k in slot_keys if slot_by_key.get(k, {}).get("required")],
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
    day_slots = _build_day_slots(req)
    diagnostics: dict[str, Any] = {
        "seed": seed,
        "model": model,
        "temperature": temperature,
        "day_slots": day_slots,
    }

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
    slot_keys = [s["key"] for s in day_slots]
    slot_by_key = {s["key"]: s for s in day_slots}
    slot_index = {k: i for i, k in enumerate(slot_keys)}

    def _repair_llm_activities(
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Enforce:
        - unique xids
        - at most one activity per slot
        - required slots must be present and match allowed_types
        Optional slot mismatches are dropped.
        """
        issues: dict[str, Any] = {}
        required_slots = {k for k in slot_keys if slot_by_key.get(k, {}).get("required")}

        normalized: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            xid = str(it.get("xid") or "")
            if not xid or xid in excluded or xid not in by_id_all:
                continue
            slot = str(it.get("slot") or "")
            if slot not in slot_by_key:
                continue
            normalized.append(
                {
                    "xid": xid,
                    "slot": slot,
                    "description": str(it.get("description") or ""),
                    "why_this": str(it.get("why_this") or ""),
                }
            )

        # Prefer required slots first, then by slot order.
        normalized.sort(
            key=lambda it: (
                0 if it["slot"] in required_slots else 1,
                slot_index.get(it["slot"], 999),
            )
        )

        chosen: list[dict[str, Any]] = []
        used_xids: set[str] = set()
        used_slots: set[str] = set()
        dropped_optional_type: list[dict[str, Any]] = []
        dropped_duplicates: list[dict[str, Any]] = []
        required_type_issues: list[dict[str, Any]] = []

        for it in normalized:
            xid = it["xid"]
            slot = it["slot"]
            if xid in used_xids or slot in used_slots:
                dropped_duplicates.append({"xid": xid, "slot": slot})
                continue

            c = by_id_all.get(xid)
            if not c:
                continue
            allowed = cast(list[str], slot_by_key[slot].get("allowed_types") or [])
            if allowed and c.inferred_type not in allowed:
                if slot in required_slots:
                    required_type_issues.append(
                        {"xid": xid, "slot": slot, "type": c.inferred_type, "allowed": allowed}
                    )
                else:
                    dropped_optional_type.append(
                        {"xid": xid, "slot": slot, "type": c.inferred_type, "allowed": allowed}
                    )
                continue

            used_xids.add(xid)
            used_slots.add(slot)
            chosen.append(it)

        missing_required = sorted(required_slots - used_slots)
        if missing_required:
            issues["missing_required_slots"] = missing_required
        if required_type_issues:
            issues["required_type_issues"] = required_type_issues
        if dropped_optional_type:
            issues["dropped_optional_type_mismatch"] = dropped_optional_type
        if dropped_duplicates:
            issues["dropped_duplicates"] = dropped_duplicates

        chosen.sort(key=lambda it: slot_index.get(it["slot"], 999))
        return chosen, issues

    def _schedule_from_slots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Deterministically schedule start/end times from slots + distances.
        This avoids trusting the LLM for timing when it can be inconsistent.
        """
        origin_lon, origin_lat = extract_origin(req.trip.geometry)
        start_min = _parse_hhmm(req.start_time)
        end_min = start_min + int(req.max_total_minutes or 0)

        cur_t = start_min
        prev_lon, prev_lat = origin_lon, origin_lat
        out: list[dict[str, Any]] = []

        for it in items:
            if cur_t >= end_min:
                break
            xid = it["xid"]
            slot = it["slot"]
            c = by_id_all.get(xid)
            if not c:
                continue

            dist_m = _haversine_m(prev_lon, prev_lat, c.location[0], c.location[1])
            travel = _estimate_drive_minutes(dist_m)

            slot_cfg = slot_by_key[slot]
            pref_start = slot_cfg.get("preferred_start_time")
            pref_end = slot_cfg.get("preferred_end_time")
            pref_start_m = (
                _parse_hhmm(pref_start) if isinstance(pref_start, str) and pref_start else None
            )
            pref_end_m = _parse_hhmm(pref_end) if isinstance(pref_end, str) and pref_end else None

            earliest = cur_t + travel
            if pref_start_m is not None:
                earliest = max(earliest, pref_start_m)
            if earliest >= end_min:
                break

            base_dur = int(default_visit_duration(c.kinds or ""))
            sug_min = int(slot_cfg.get("suggested_duration_min") or base_dur)
            sug_max = int(slot_cfg.get("suggested_duration_max") or base_dur)
            dur = max(sug_min, min(sug_max, base_dur))

            start_at = earliest
            end_at = start_at + dur

            if pref_end_m is not None and end_at > pref_end_m and dur > sug_min:
                end_at = max(start_at + sug_min, min(pref_end_m, end_at))

            if end_at > end_min:
                end_at = end_min
            if end_at <= start_at:
                end_at = min(end_min, start_at + 15)

            out.append(
                {
                    **it,
                    "start_time": _format_hhmm(start_at),
                    "end_time": _format_hhmm(end_at),
                    "travel_minutes_from_prev": travel,
                }
            )
            cur_t = end_at
            prev_lon, prev_lat = c.location[0], c.location[1]

        return out

    def _validate_slots_and_times(items: list[dict[str, Any]]) -> dict[str, Any]:
        violations: dict[str, Any] = {}
        required = {k for k in slot_keys if slot_by_key.get(k, {}).get("required")}
        seen_slots: list[str] = []
        seen_xids: set[str] = set()
        bad_slots: list[dict[str, Any]] = []
        time_issues: list[dict[str, Any]] = []
        type_issues: list[dict[str, Any]] = []
        duplicate_xids: list[dict[str, Any]] = []

        start_min = _parse_hhmm(req.start_time)
        end_min = start_min + int(req.max_total_minutes or 0)

        for it in items:
            xid = str(it.get("xid") or "")
            slot = str(it.get("slot") or "")
            st = str(it.get("start_time") or "")
            en = str(it.get("end_time") or "")

            if xid and xid in seen_xids:
                duplicate_xids.append({"xid": xid, "slot": slot})
            if xid:
                seen_xids.add(xid)

            if slot not in slot_by_key:
                bad_slots.append({"xid": xid, "slot": slot})
                continue
            if slot in seen_slots:
                bad_slots.append({"xid": xid, "slot": slot, "reason": "duplicate_slot"})
            seen_slots.append(slot)

            try:
                st_m = _parse_hhmm(st)
                en_m = _parse_hhmm(en)
                if st_m >= en_m:
                    time_issues.append({"xid": xid, "start_time": st, "end_time": en})
                if st_m < start_min or en_m > end_min:
                    time_issues.append(
                        {
                            "xid": xid,
                            "start_time": st,
                            "end_time": en,
                            "reason": "outside_day_bounds",
                        }
                    )
            except ValueError:
                time_issues.append(
                    {"xid": xid, "start_time": st, "end_time": en, "reason": "parse"}
                )

            c = by_id_all.get(xid)
            if c:
                allowed = slot_by_key[slot].get("allowed_types") or []
                if allowed and c.inferred_type not in allowed:
                    type_issues.append(
                        {"xid": xid, "slot": slot, "type": c.inferred_type, "allowed": allowed}
                    )

        missing_required = sorted(required - set(seen_slots))

        if bad_slots:
            violations["slot_issues"] = bad_slots
        if missing_required:
            violations["missing_required_slots"] = missing_required
        if time_issues:
            violations["time_issues"] = time_issues
        if type_issues:
            violations["type_issues"] = type_issues
        if duplicate_xids:
            violations["duplicate_xids"] = duplicate_xids
        return violations

    slot_violations = _validate_slots_and_times(activities_in)

    if invalid or missing_locked or slot_violations:
        fix_msg = {
            "invalid_xids": invalid,
            "missing_locked_xids": missing_locked,
            "excluded_xids": sorted(excluded),
            "slot_violations": slot_violations,
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

        # If still violating constraints, do one more correction pass.
        slot_violations2 = _validate_slots_and_times(activities_in)
        chosen_xids2 = [
            str(a.get("xid"))
            for a in activities_in
            if isinstance(a, dict) and a.get("xid") is not None
        ]
        invalid2 = [x for x in chosen_xids2 if x and (x not in by_id_all or x in excluded)]
        missing_locked2 = [
            x for x in locked if x not in chosen_xids2 and x in by_id_all and x not in excluded
        ]
        if invalid2 or missing_locked2 or slot_violations2:
            fix_msg2 = {
                "invalid_xids": invalid2,
                "missing_locked_xids": missing_locked2,
                "excluded_xids": sorted(excluded),
                "slot_violations": slot_violations2,
                "extra_rules": [
                    "Do NOT swap descriptions between places.",
                    "For each activity, description must be about that activity's candidate name.",
                    "Candidate type MUST match the slot allowed_types.",
                ],
            }
            parsed = await _llm_build(
                tmp_req,
                seed=seed,
                model=model,
                temperature=temperature,
                base_url=base_url,
                api_key=api_key,
                candidates_payload=candidates_payload,
                attempt=3,
                fix_message=json.dumps(fix_msg2),
            )
            day_obj = cast(dict[str, Any], parsed.get("day") or {})
            activities_in_any = day_obj.get("activities")
            activities_in = (
                cast(list[dict[str, Any]], activities_in_any)
                if isinstance(activities_in_any, list)
                else []
            )

    repaired, repair_issues = _repair_llm_activities(activities_in)
    diagnostics["repair"] = repair_issues

    def _fill_missing_slots(
        items: list[dict[str, Any]],
        *,
        include_optional: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Fill any missing slots deterministically from candidates.
        This prevents hard failures like "missing lunch" and also avoids returning only 1–2 stops
        when the LLM omits optional slots.
        """
        required_slots = [k for k in slot_keys if slot_by_key.get(k, {}).get("required")]
        used_slots = {it["slot"] for it in items}
        used_xids = {it["xid"] for it in items}
        missing_required = [k for k in required_slots if k not in used_slots]
        missing_optional = [
            k for k in slot_keys if k not in used_slots and k not in set(required_slots)
        ]

        filled: list[dict[str, Any]] = list(items)
        fill_diag: dict[str, Any] = {
            "missing_required_slots": missing_required,
            "missing_optional_slots": missing_optional if include_optional else [],
            "filled": [],
        }

        for slot in slot_keys:
            if slot in used_slots:
                continue
            is_required = slot in required_slots
            if not is_required and not include_optional:
                continue
            allowed = cast(list[str], slot_by_key[slot].get("allowed_types") or [])
            if not allowed:
                continue
            # Pick the best available candidate by score.
            eligible: list[Candidate] = []
            for c in tmp_req.candidates:
                if not c.xid or c.xid in excluded or c.xid in used_xids:
                    continue
                if c.inferred_type in allowed:
                    eligible.append(c)
            eligible.sort(key=_score, reverse=True)
            if not eligible:
                continue
            pick = eligible[0]
            used_xids.add(pick.xid)
            used_slots.add(slot)
            filled.append(
                {
                    "xid": pick.xid,
                    "slot": slot,
                    "description": f"Stop at {pick.name}.",
                    "why_this": f"Picked to satisfy the {slot_by_key[slot].get('label') or slot} slot.",
                }
            )
            fill_diag["filled"].append(
                {"slot": slot, "xid": pick.xid, "type": pick.inferred_type, "name": pick.name}
            )

        # Keep at most one activity per slot (required wins due to being added last).
        dedup_by_slot: dict[str, dict[str, Any]] = {}
        for it in filled:
            dedup_by_slot[str(it["slot"])] = it
        out = list(dedup_by_slot.values())
        out.sort(key=lambda it: slot_index.get(str(it["slot"]), 999))
        return out, fill_diag

    repaired, fill_diag = _fill_missing_slots(repaired, include_optional=True)
    if fill_diag.get("filled"):
        diagnostics["fill"] = fill_diag

    scheduled = _schedule_from_slots(repaired)

    title = cast(str | None, parsed.get("title"))
    summary = cast(str | None, parsed.get("summary"))

    activities_out: list[PlannedActivity] = []
    for a in scheduled:
        xid = str(a.get("xid") or "")
        slot_key = str(a.get("slot") or "")
        if not xid or xid in excluded:
            continue
        c = by_id_all.get(xid)
        if not c:
            continue
        slot_label = None
        for s in day_slots:
            if s.get("key") == slot_key:
                slot_label = cast(str | None, s.get("label"))
                break
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
                travel_minutes_from_prev=cast(int | None, a.get("travel_minutes_from_prev")),
                segment=slot_label or None,
            )
        )

    if not activities_out:
        raise ValueError("LLM did not return any valid activities")

    # Drive estimates were computed during scheduling.

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
