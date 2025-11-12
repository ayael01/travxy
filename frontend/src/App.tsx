import { useState } from "react";
import MapPicker from "./components/MapPicker";
import { planTrip } from "./services/api";
import type { Activity, PlanResponse, TripRequest } from "./types";


function ActivityRow({ a }: { a: Activity }) {
  const [lon, lat] = a.location;
  return (
    <div className="flex items-center justify-between rounded-lg border p-3">
      <div>
        <div className="font-semibold">{a.name}</div>
        <div className="text-sm text-gray-500">
          {a.type} • {a.duration_minutes} min • {lat.toFixed(5)},{lon.toFixed(5)}
        </div>
      </div>
      {a.source_id ? (
        <span className="text-xs text-gray-400">id: {a.source_id.slice(0, 10)}…</span>
      ) : null}
    </div>
  );
}

export default function App() {
  const [lon, setLon] = useState(34.80999280643561);
  const [lat, setLat] = useState(30.61708778782791);
  const [interests, setInterests] = useState("views,food,culture");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<PlanResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function onPlan(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setErr(null);
    setData(null);
    const payload: TripRequest = {
      geometry: { type: "Point", coordinates: [Number(lon), Number(lat)] },
      days: 1,
      pace: "moderate",
      interests: interests.split(",").map((s) => s.trim()).filter(Boolean),
      travel_mode: "car",
    };
    try {
      const res = await planTrip(payload);
      setData(res);
    } catch (e: any) {
      setErr(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  const activities = data?.itinerary?.[0]?.activities ?? [];

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-2xl font-bold">Travxy — Day Planner (MVP)</h1>

      <form onSubmit={onPlan} className="mb-6 grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-600">Longitude</span>
          <input
            className="rounded border p-2"
            type="number"
            step="0.0001"
            value={lon}
            onChange={(e) => setLon(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-600">Latitude</span>
          <input
            className="rounded border p-2"
            type="number"
            step="0.0001"
            value={lat}
            onChange={(e) => setLat(Number(e.target.value))}
          />
        </label>
        <label className="col-span-2 flex flex-col gap-1">
          <span className="text-sm text-gray-600">Interests (comma-separated)</span>
          <input
            className="rounded border p-2"
            type="text"
            value={interests}
            onChange={(e) => setInterests(e.target.value)}
            placeholder="hiking,views,food"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="col-span-2 rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          {loading ? "Planning…" : "Plan day"}
        </button>
      </form>

      <MapPicker
        lon={Number(lon)}
        lat={Number(lat)}
        onChange={(newLon, newLat) => {
          setLon(newLon);
          setLat(newLat);
        }}
      />

      {err && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-red-700">
          {err}
        </div>
      )}

      {data && (
        <div className="space-y-3">
          <div className="text-sm text-gray-500">
            radius: {data.query?.["radius_m"] as number | undefined ?? "?"} m • status: {data.status}
          </div>
          {activities.length === 0 ? (
            <div className="rounded border p-3">No activities found.</div>
          ) : (
            activities.map((a, idx) => <ActivityRow key={idx} a={a} />)
          )}
        </div>
      )}
    </div>
  );
}
