export type ActivityType = "hiking" | "restaurant" | "attraction" | "viewpoint" | "lodging";

export interface Activity {
    type: ActivityType;
    name: string;
    duration_minutes: number;
    location: [number, number]; // [lon, lat]
    source_id?: string | null;
    notes?: string | null;
}

export interface DayPlan {
    day: number;
    total_duration_hours: number;
    activities: Activity[];
}

export interface PlanResponse {
    query: Record<string, unknown>;
    itinerary: DayPlan[];
    status: "ok" | "error";
}

export interface TripRequest {
    geometry: { type: "Point"; coordinates: [number, number] };
    days: number;
    pace: "easy" | "moderate" | "intense";
    interests: string[];
    travel_mode: "car" | "foot" | "bike";
    budget?: "basic" | "mid" | "luxury" | null;
    date?: string | null;
}
