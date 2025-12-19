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

export interface TripRequest {
    geometry: { type: "Point"; coordinates: [number, number] };
    days: number;
    pace: "easy" | "moderate" | "intense";
    interests: string[];
    travel_mode: "car" | "foot" | "bike";
    budget?: "basic" | "mid" | "luxury" | null;
    date?: string | null;
}
