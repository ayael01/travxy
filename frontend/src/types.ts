export type ActivityType = "hiking" | "restaurant" | "attraction" | "viewpoint" | "lodging";

export interface Candidate {
    xid?: string | null;
    name: string;
    location: [number, number]; // [lon, lat]
    distance_m?: number | null;
    inferred_type: ActivityType;
    kinds?: string | null;
    categories?: string[];
    rating?: number | null;
    user_ratings_total?: number | null;
    provider?: string | null;
}

export interface CandidatesResponse {
    query: Record<string, unknown>;
    candidates: Candidate[];
    status: "ok" | "error";
}

export interface PlannedActivity {
    xid?: string | null;
    type: ActivityType;
    name: string;
    duration_minutes: number;
    location: [number, number]; // [lon, lat]
    provider?: string | null;
    description?: string | null;
    why_this?: string | null;
    notes?: string | null;
    start_time?: string | null; // "HH:MM"
    end_time?: string | null; // "HH:MM"
    travel_minutes_from_prev?: number | null;
    segment?: string | null;
}

export interface DayPlan {
    day: number;
    title?: string | null;
    overview?: string | null;
    tips?: string[];
    total_duration_hours: number;
    activities: PlannedActivity[];
}

export interface ItineraryResponse {
    query: Record<string, unknown>;
    title?: string | null;
    summary?: string | null;
    itinerary: DayPlan[];
    status: "ok" | "error";
}

export interface ItineraryBuildRequest {
    trip: TripRequest;
    candidates: Candidate[];
    locked_xids?: string[];
    excluded_xids?: string[];
    previous_itineraries?: string[][];
    variant?: number;
    max_stops?: number;
    start_time?: string;
    max_total_minutes?: number;
}

export interface TripRequest {
    geometry: { type: "Point"; coordinates: [number, number] };
    radius_m?: number | null;
    days: number;
    pace: "easy" | "moderate" | "intense";
    interests: string[];
    travel_mode: "car" | "foot" | "bike";
    budget?: "basic" | "mid" | "luxury" | null;
    date?: string | null;
}
