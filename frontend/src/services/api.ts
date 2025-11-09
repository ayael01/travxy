import type { PlanResponse, TripRequest } from "../types";

const API_BASE = "http://127.0.0.1:8000";

export async function planTrip(payload: TripRequest): Promise<PlanResponse> {
    const res = await fetch(`${API_BASE}/plan_trip`, {
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
