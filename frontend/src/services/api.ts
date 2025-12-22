import type { CandidatesResponse, ItineraryBuildRequest, ItineraryResponse, TripRequest } from "../types";

const API_BASE = "http://127.0.0.1:8000";

export async function planTrip(payload: TripRequest, limit: number = 200): Promise<CandidatesResponse> {
    const url = new URL(`${API_BASE}/plan_trip`);
    url.searchParams.set("limit", String(limit));
    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return res.json();
}

export async function buildItinerary(payload: ItineraryBuildRequest): Promise<ItineraryResponse> {
    const res = await fetch(`${API_BASE}/build_itinerary`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return res.json();
}
