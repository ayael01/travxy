import type { Map as LeafletMap } from "leaflet";
import { useState } from "react";
import MapPicker from "./components/MapPicker";
import { planTrip } from "./services/api";
import { geocode, type GeocodeResult } from "./services/geocode";
import type { Activity, PlanResponse, TripRequest } from "./types";

function ActivityRow({ a }: { a: Activity }) {
  const [lon, lat] = a.location;

  const typeMeta: Record<
    Activity["type"],
    { label: string; emoji: string; color: string }
  > = {
    hiking: { label: "Hiking", emoji: "🥾", color: "#22c55e" },
    restaurant: { label: "Food", emoji: "🍽️", color: "#f97316" },
    attraction: { label: "Attraction", emoji: "📍", color: "#3b82f6" },
    viewpoint: { label: "Viewpoint", emoji: "🌄", color: "#a855f7" },
    lodging: { label: "Lodging", emoji: "🛏️", color: "#eab308" },
  };

  const meta = typeMeta[a.type];
  const mapsUrl = `https://www.google.com/maps?q=${lat},${lon}`;

  return (
    <div className="activity-card" style={{ borderLeftColor: meta.color }}>
      <div className="activity-main">
        <div className="activity-icon">
          <span>{meta.emoji}</span>
        </div>
        <div className="activity-text">
          <div className="activity-title">{a.name}</div>
          <div className="activity-sub">
            <span className="activity-pill">{meta.label}</span>
            <span>• {a.duration_minutes} min</span>
            <a
              href={mapsUrl}
              target="_blank"
              rel="noreferrer"
              className="activity-link"
            >
              View on map
            </a>
          </div>
          <div className="activity-coords">
            {lat.toFixed(5)}, {lon.toFixed(5)}
          </div>
        </div>
      </div>
      {a.source_id ? (
        <div className="activity-meta">id: {a.source_id.slice(0, 10)}…</div>
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
  const [settingsOpen, setSettingsOpen] = useState(true);

  // Map instance (so we can pan/zoom after search)
  const [map, setMap] = useState<LeafletMap | null>(null);

  // Search area (geocoding)
  const [search, setSearch] = useState("");
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<GeocodeResult[]>([]);

  async function onPlan(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setErr(null);
    setData(null);

    const payload: TripRequest = {
      geometry: { type: "Point", coordinates: [Number(lon), Number(lat)] },
      days: 1,
      pace: "moderate",
      interests: interests
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
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

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setSearchLoading(true);
    setSearchErr(null);
    setSearchResults([]);

    try {
      const res = await geocode(search);
      setSearchResults(res);

      // Auto-jump to the first result (user can still pick another)
      if (res[0]) {
        chooseResult(res[0]);
      }
    } catch (e: any) {
      setSearchErr(e.message || "Search failed");
    } finally {
      setSearchLoading(false);
    }
  }

  function chooseResult(r: GeocodeResult) {
    const newLat = Number(r.lat);
    const newLon = Number(r.lon);

    if (Number.isFinite(newLat) && Number.isFinite(newLon)) {
      if (map) {
        map.setView([newLat, newLon], 13);
      }
      setLat(newLat);
      setLon(newLon);
    }

    // Keep results visible if you want; for MVP it's nicer to collapse.
    setSearchResults([]);
  }

  const day = data?.itinerary?.[0];
  const activities = day?.activities ?? [];
  const radius = (data?.query?.["radius_m"] as number | undefined) ?? undefined;

  return (
    <div className="app-shell">
      <div className="app-noise" />
      <div className="app-inner">
        <header className="app-header">
          <div className="app-logo">
            <span className="app-logo-mark">T</span>
            <div className="app-logo-text">
              <span className="app-logo-title">Travxy</span>
              <span className="app-logo-sub">Draw a point. Get a day.</span>
            </div>
          </div>
          <div className="app-header-tags">
            <span className="badge badge-soft">MVP</span>
            <span className="badge">AI day trip planner</span>
          </div>
        </header>

        <main className="app-main">
          {/* Map + floating settings */}
          <section className="panel panel-map-full">
            <div className="panel-map-header">
              <h2 className="panel-title">Pick your spot</h2>
              <p className="panel-desc">
                Search an area to jump the map, then click anywhere to set your
                starting point. Travxy will search up to 15 km around that point
                and build a route.
              </p>
            </div>

            <div className="map-wrapper">
              <MapPicker
                lon={Number(lon)}
                lat={Number(lat)}
                onChange={(newLon, newLat) => {
                  setLon(newLon);
                  setLat(newLat);
                }}
                onMapReady={(m) => setMap(m)}
              />

              {/* Floating trip settings card */}
              <div
                className={
                  "settings-float" +
                  (settingsOpen
                    ? " settings-float-open"
                    : " settings-float-closed")
                }
              >
                <div className="settings-float-header">
                  <h3 className="panel-title">Trip settings</h3>
                  <button
                    type="button"
                    className="settings-float-close"
                    onClick={() => setSettingsOpen(false)}
                    aria-label="Hide trip settings"
                  >
                    ✕
                  </button>
                </div>

                <p className="panel-desc settings-float-desc">
                  Search an area, adjust the starting point, choose interests,
                  then generate your day.
                </p>

                {/* Area search */}
                <form onSubmit={onSearch} className="form-grid">
                  <label className="form-label">
                    <span>Search area</span>
                    <input
                      className="input"
                      type="text"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Tel Aviv, Central Park, Eilat..."
                    />
                    <div className="form-hint">
                      Type a place name/address and hit Go. Then click on the
                      map to refine the exact point.
                    </div>
                  </label>

                  <button
                    type="submit"
                    disabled={searchLoading || !search.trim()}
                    className="btn-primary"
                  >
                    {searchLoading ? "Searching..." : "Go"}
                  </button>

                  {searchErr && (
                    <div className="alert alert-error">
                      <strong>Oops.</strong> {searchErr}
                    </div>
                  )}

                  {searchResults.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {searchResults.map((r, idx) => (
                        <button
                          key={`${r.lat}-${r.lon}-${idx}`}
                          type="button"
                          onClick={() => chooseResult(r)}
                          className="activity-card"
                          style={{ textAlign: "left", cursor: "pointer" }}
                        >
                          {r.display_name}
                        </button>
                      ))}
                    </div>
                  )}
                </form>

                {/* Existing trip settings */}
                <form onSubmit={onPlan} className="form-grid" style={{ marginTop: 12 }}>
                  <div className="form-row">
                    <label className="form-label">
                      <span>Longitude</span>
                      <input
                        className="input"
                        type="number"
                        step="0.0001"
                        value={lon}
                        onChange={(e) => setLon(Number(e.target.value))}
                      />
                    </label>
                    <label className="form-label">
                      <span>Latitude</span>
                      <input
                        className="input"
                        type="number"
                        step="0.0001"
                        value={lat}
                        onChange={(e) => setLat(Number(e.target.value))}
                      />
                    </label>
                  </div>

                  <label className="form-label">
                    <span>Interests</span>
                    <input
                      className="input"
                      type="text"
                      value={interests}
                      onChange={(e) => setInterests(e.target.value)}
                      placeholder="hiking,views,food"
                    />
                    <div className="form-hint">
                      Comma separated - for now we keep it simple.
                    </div>
                  </label>

                  <button type="submit" disabled={loading} className="btn-primary">
                    {loading ? "Planning your perfect day..." : "Plan my day"}
                  </button>

                  {err && (
                    <div className="alert alert-error">
                      <strong>Oops.</strong> {err}
                    </div>
                  )}

                  {data && (
                    <div className="summary-chip">
                      <span>
                        Radius: {radius ? `${radius} m` : "unknown"} • Status:{" "}
                        {data.status}
                      </span>
                      {day && (
                        <span>
                          Total: {day.total_duration_hours.toFixed(1)} h •{" "}
                          {activities.length} stops
                        </span>
                      )}
                    </div>
                  )}
                </form>
              </div>

              {/* Small pill to reopen settings when they are closed */}
              {!settingsOpen && (
                <button
                  type="button"
                  className="settings-toggle"
                  onClick={() => setSettingsOpen(true)}
                >
                  Trip settings
                </button>
              )}
            </div>
          </section>

          {/* Itinerary section */}
          <section className="panel panel-itinerary">
            <div className="panel-itinerary-header">
              <div>
                <h2 className="panel-title">Your day plan</h2>
                <p className="panel-desc">
                  Activities are ordered for a smooth, realistic flow. You can
                  open each stop in Google Maps.
                </p>
              </div>
              {day && (
                <div className="day-badge">
                  Day {day.day} • {day.total_duration_hours.toFixed(1)} h
                </div>
              )}
            </div>

            {!data && (
              <div className="empty-state">
                <div className="empty-title">No plan yet</div>
                <div className="empty-sub">
                  Search an area, drop a pin, and hit "Plan my day".
                </div>
              </div>
            )}

            {data && activities.length === 0 && (
              <div className="empty-state">
                <div className="empty-title">No activities found</div>
                <div className="empty-sub">
                  Try moving the point closer to a city or adjusting your
                  interests.
                </div>
              </div>
            )}

            {activities.length > 0 && (
              <div className="timeline">
                {activities.map((a, idx) => (
                  <ActivityRow key={idx} a={a} />
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
