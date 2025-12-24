import type { Map as LeafletMap } from "leaflet";
import { useEffect, useState } from "react";
import MapPicker from "./components/MapPicker";
import { buildItinerary, planTrip } from "./services/api";
import { geocode, type GeocodeResult } from "./services/geocode";
import type {
  Candidate,
  CandidatesResponse,
  ItineraryResponse,
  PlannedActivity,
  TripRequest,
} from "./types";

function CandidateRow({
  c,
  locked,
  onToggleLock,
}: {
  c: Candidate;
  locked: boolean;
  onToggleLock: (xid: string) => void;
}) {
  const [lon, lat] = c.location;
  const mapsUrl = `https://www.google.com/maps?q=${lat},${lon}`;
  const rating =
    c.rating != null
      ? `${c.rating}${c.user_ratings_total != null ? ` (${c.user_ratings_total})` : ""}`
      : null;

  return (
    <div className="activity-card">
      <div className="activity-main">
        <div className="activity-icon">
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={locked}
              disabled={!c.xid}
              onChange={() => c.xid && onToggleLock(c.xid)}
              aria-label="Lock this candidate"
            />
            <span>•</span>
          </label>
        </div>
        <div className="activity-text">
          <div className="activity-title">{c.name}</div>
          <div className="activity-sub">
            <span className="activity-pill">{c.inferred_type}</span>
            {typeof c.distance_m === "number" ? (
              <span>• {(c.distance_m / 1000).toFixed(2)} km</span>
            ) : null}
            {rating ? <span>• rating {rating}</span> : null}
            <a href={mapsUrl} target="_blank" rel="noreferrer" className="activity-link">
              View on map
            </a>
          </div>
          <div className="activity-coords">
            {lat.toFixed(5)}, {lon.toFixed(5)}
          </div>
        </div>
      </div>
    </div>
  );
}

function PlannedActivityRow({ a }: { a: PlannedActivity }) {
  const [lon, lat] = a.location;
  const mapsUrl = `https://www.google.com/maps?q=${lat},${lon}`;
  const time =
    a.start_time && a.end_time ? `${a.start_time}–${a.end_time}` : undefined;

  return (
    <div className="activity-card">
      <div className="activity-main">
        <div className="activity-icon">
          <span>•</span>
        </div>
        <div className="activity-text">
          <div className="activity-title">{a.name}</div>
          <div className="activity-sub">
            <span className="activity-pill">{a.type}</span>
            {a.segment ? <span>• {a.segment}</span> : null}
            {time ? <span>• {time}</span> : null}
            <span>• {a.duration_minutes} min</span>
            {typeof a.travel_minutes_from_prev === "number" ? (
              <span>• drive {a.travel_minutes_from_prev} min</span>
            ) : null}
            <a href={mapsUrl} target="_blank" rel="noreferrer" className="activity-link">
              View on map
            </a>
          </div>
          {a.description ? <div className="activity-meta">{a.description}</div> : null}
          {a.why_this ? <div className="activity-meta">Why: {a.why_this}</div> : null}
          <div className="activity-coords">
            {lat.toFixed(5)}, {lon.toFixed(5)}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [lon, setLon] = useState(34.80999280643561);
  const [lat, setLat] = useState(30.61708778782791);
  const [interests, setInterests] = useState("views,food,culture");
  const [radiusM, setRadiusM] = useState(15000);
  const [loading, setLoading] = useState(false);
  const [candidatesData, setCandidatesData] = useState<CandidatesResponse | null>(null);
  const [itineraryData, setItineraryData] = useState<ItineraryResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [lockedXids, setLockedXids] = useState<Set<string>>(new Set());
  const [variant, setVariant] = useState(0);
  const [resultsTab, setResultsTab] = useState<"itinerary" | "candidates">("itinerary");
  const [candidateTypeFilter, setCandidateTypeFilter] = useState<string>("all");
  const [previousItineraries, setPreviousItineraries] = useState<string[][]>([]);
  const [lastTripKey, setLastTripKey] = useState<string>("");
  const [itineraryOrigin, setItineraryOrigin] = useState<{ lon: number; lat: number } | null>(
    null,
  );

  // Map instance (so we can pan/zoom after search)
  const [map, setMap] = useState<LeafletMap | null>(null);

  // Search area (geocoding)
  const [search, setSearch] = useState("");
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<GeocodeResult[]>([]);

  function buildTripPayload(): TripRequest {
    return {
      geometry: { type: "Point", coordinates: [Number(lon), Number(lat)] },
      radius_m: radiusM,
      days: 1,
      pace: "moderate",
      interests: interests
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      travel_mode: "car",
    };
  }

  function tripKeyFor(t: TripRequest): string {
    return JSON.stringify({
      lon: Number(t.geometry.coordinates[0]).toFixed(6),
      lat: Number(t.geometry.coordinates[1]).toFixed(6),
      radius_m: t.radius_m ?? null,
      interests: [...t.interests].sort(),
      pace: t.pace,
      travel_mode: t.travel_mode,
      days: t.days,
    });
  }

  async function runItineraryBuild(tripPayload: TripRequest, cands: CandidatesResponse, v: number) {
    const locked = Array.from(lockedXids).filter((xid) =>
      cands.candidates.some((c) => c.xid === xid),
    );

    const res = await buildItinerary({
      trip: tripPayload,
      candidates: cands.candidates,
      locked_xids: locked,
      excluded_xids: [],
      previous_itineraries: previousItineraries,
      variant: v,
      max_stops: 6,
      start_time: "09:30",
      max_total_minutes: 7 * 60,
    });

    setItineraryData(res);
    setItineraryOrigin({
      lon: Number(tripPayload.geometry.coordinates[0]),
      lat: Number(tripPayload.geometry.coordinates[1]),
    });
    const xids = (res.itinerary?.[0]?.activities ?? [])
      .map((a) => a.xid)
      .filter((x): x is string => Boolean(x));
    if (xids.length > 0) {
      setPreviousItineraries((prev) => [...prev, xids]);
    }
  }

  async function onPlan(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setErr(null);

    const tripPayload = buildTripPayload();
    const key = tripKeyFor(tripPayload);

    try {
      let cands = candidatesData;
      let v = variant;

      // If trip inputs changed, refetch candidates and reset rebuild state.
      if (!cands || lastTripKey !== key) {
        setCandidatesData(null);
        setItineraryData(null);
        setItineraryOrigin(null);
        cands = await planTrip(tripPayload, 200);
        setCandidatesData(cands);
        setLockedXids(new Set());
        setPreviousItineraries([]);
        v = 0;
        setVariant(0);
        setLastTripKey(key);
      } else {
        // Same trip: regenerate a new variant using existing candidates.
        v = v + 1;
        setVariant(v);
      }

      await runItineraryBuild(tripPayload, cands, v);
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

  const radiusLabel = radiusM % 1000 === 0 ? `${radiusM / 1000} km` : `${radiusM} m`;
  const candidates = candidatesData?.candidates ?? [];
  const day = itineraryData?.itinerary?.[0];
  const planned = day?.activities ?? [];
  const hasItinerary = planned.length > 0;
  const hasCandidates = candidates.length > 0;

  const candidateTypeCounts = new Map<string, number>();
  for (const c of candidates) {
    const t = c.inferred_type;
    if (!t) continue;
    candidateTypeCounts.set(t, (candidateTypeCounts.get(t) ?? 0) + 1);
  }
  const candidateTypes = Array.from(candidateTypeCounts.keys()).sort();
  const candidateTypesKey = candidateTypes.join("|");
  const visibleCandidates =
    candidateTypeFilter === "all"
      ? candidates
      : candidates.filter((c) => c.inferred_type === candidateTypeFilter);

  // Pick a sensible default tab based on what data exists.
  useEffect(() => {
    if (hasItinerary) setResultsTab("itinerary");
    else if (hasCandidates) setResultsTab("candidates");
  }, [hasItinerary, hasCandidates]);

  // Reset/validate candidate filter when a new batch arrives.
  useEffect(() => {
    setCandidateTypeFilter("all");
  }, [candidatesData?.candidates.length]);

  useEffect(() => {
    if (candidateTypeFilter !== "all" && !candidateTypeCounts.has(candidateTypeFilter)) {
      setCandidateTypeFilter("all");
    }
  }, [candidateTypeFilter, candidateTypesKey]);

  const radiusMin = 500;
  const radiusMax = 50000;
  const radiusStep = 500;

  function clampRadius(v: number): number {
    return Math.min(radiusMax, Math.max(radiusMin, v));
  }

  function toggleLock(xid: string) {
    setLockedXids((prev) => {
      const next = new Set(prev);
      if (next.has(xid)) next.delete(xid);
      else next.add(xid);
      return next;
    });
  }

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
                starting point. Travxy will search up to {radiusLabel} around that
                point and build a route.
              </p>
            </div>

            <div className="map-wrapper">
              <MapPicker
                lon={Number(lon)}
                lat={Number(lat)}
                radiusM={radiusM}
                itinerary={planned}
                itineraryOrigin={itineraryOrigin ?? undefined}
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

	                <div className="settings-float-body">
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
	                      <span>Radius ({radiusLabel})</span>
	                      <div className="radius-row">
	                        <button
	                          type="button"
	                          className="radius-step-btn"
	                          onClick={() => setRadiusM((v) => clampRadius(v - radiusStep))}
	                          aria-label="Decrease radius"
	                        >
	                          −
	                        </button>
	                        <input
	                          className="input radius-slider"
	                          type="range"
	                          min={radiusMin}
	                          max={radiusMax}
	                          step={radiusStep}
	                          value={radiusM}
	                          onChange={(e) => setRadiusM(clampRadius(Number(e.target.value)))}
	                        />
	                        <button
	                          type="button"
	                          className="radius-step-btn"
	                          onClick={() => setRadiusM((v) => clampRadius(v + radiusStep))}
	                          aria-label="Increase radius"
	                        >
	                          +
	                        </button>
	                      </div>
	                      <div className="form-hint">Drag to adjust the search radius.</div>
	                    </label>

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
	                      {loading ? "Planning..." : "Plan my day"}
	                    </button>

	                    {err && (
	                      <div className="alert alert-error">
	                        <strong>Oops.</strong> {err}
	                      </div>
	                    )}

	                    {candidatesData && (
	                      <div className="summary-chip">
	                        <span>
	                          Radius: {radiusM} m • Status: {candidatesData?.status}
	                        </span>
	                        <span>
	                          Candidates: {candidates.length} • Provider:{" "}
	                          {(candidatesData.query?.["provider"] as string) ?? "unknown"}
	                        </span>
	                        <span>
	                          Locked: {lockedXids.size} • Variant: {variant}
	                        </span>
	                      </div>
	                    )}
	                  </form>
	                </div>
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

	          {/* Results section */}
	          <section className="panel panel-itinerary">
	            <div className="panel-itinerary-header">
	              <div>
	                <h2 className="panel-title">
	                  {resultsTab === "itinerary" ? "Itinerary" : "Candidates"}
	                </h2>
	                <p className="panel-desc">
	                  {resultsTab === "itinerary"
	                    ? "AI stage: planned day using the fetched candidates."
	                    : "Raw places fetched from providers (with inferred type)."}
	                </p>
	              </div>
	              <div className="results-tabs" role="tablist" aria-label="Results view">
	                <button
	                  type="button"
	                  role="tab"
	                  aria-selected={resultsTab === "itinerary"}
	                  className={
	                    "results-tab" + (resultsTab === "itinerary" ? " results-tab-active" : "")
	                  }
	                  onClick={() => setResultsTab("itinerary")}
	                >
	                  Itinerary
	                  {hasItinerary ? (
	                    <span className="results-tab-badge">{planned.length}</span>
	                  ) : null}
	                </button>
	                <button
	                  type="button"
	                  role="tab"
	                  aria-selected={resultsTab === "candidates"}
	                  className={
	                    "results-tab" + (resultsTab === "candidates" ? " results-tab-active" : "")
	                  }
	                  onClick={() => setResultsTab("candidates")}
	                >
	                  Candidates
	                  {hasCandidates ? (
	                    <span className="results-tab-badge">{candidates.length}</span>
	                  ) : null}
	                </button>
	              </div>
	            </div>

	            {!candidatesData && (
	              <div className="empty-state">
	                <div className="empty-title">No data yet</div>
                <div className="empty-sub">
                  Search an area, drop a pin, and hit "Plan my day" to fetch
                  candidates for the AI stage.
                </div>
              </div>
            )}

	            {candidatesData && candidates.length === 0 && resultsTab === "candidates" && (
	              <div className="empty-state">
	                <div className="empty-title">No candidates found</div>
	                <div className="empty-sub">Try moving the point or increasing backend limit.</div>
	              </div>
	            )}

	            {resultsTab === "candidates" && candidates.length > 0 && visibleCandidates.length === 0 && (
	              <div className="empty-state">
	                <div className="empty-title">No matches</div>
	                <div className="empty-sub">Try changing the candidate type filter.</div>
	              </div>
	            )}

	            {candidatesData && resultsTab === "itinerary" && !hasItinerary && (
	              <div className="empty-state">
	                <div className="empty-title">No itinerary</div>
	                <div className="empty-sub">
	                  Click “Plan my day” to generate an itinerary from the fetched candidates.
	                </div>
	              </div>
	            )}

	            {resultsTab === "itinerary" && planned.length > 0 && (
	              <div className="timeline">
	                {itineraryData?.title ? (
	                  <div className="activity-card">
	                    <div className="activity-title">{itineraryData.title}</div>
                    {itineraryData.summary ? (
                      <div className="activity-meta">{itineraryData.summary}</div>
                    ) : null}
                    {day?.title ? <div className="activity-meta">{day.title}</div> : null}
                    {day?.overview ? <div className="activity-meta">{day.overview}</div> : null}
                    {day?.tips && day.tips.length > 0 ? (
                      <div className="activity-meta">
                        Tips: {day.tips.slice(0, 6).join(" • ")}
                      </div>
                    ) : null}
                  </div>
                ) : null}
	                {planned.map((a, idx) => (
	                  <PlannedActivityRow key={`${a.xid ?? "noid"}-${idx}`} a={a} />
	                ))}
	              </div>
	            )}

	            {resultsTab === "candidates" && visibleCandidates.length > 0 && (
	              <div className="timeline">
	                <div className="activity-card">
	                  <div className="activity-title">
	                    Candidates{itineraryData ? " (lock to force inclusion)" : ""}
	                  </div>
	                  <div className="candidates-toolbar">
	                    <label className="candidates-filter">
	                      <span className="candidates-filter-label">Type</span>
	                      <select
	                        className="input candidates-filter-select"
	                        value={candidateTypeFilter}
	                        onChange={(e) => setCandidateTypeFilter(e.target.value)}
	                      >
	                        <option value="all">All ({candidates.length})</option>
	                        {candidateTypes.map((t) => (
	                          <option key={t} value={t}>
	                            {t} ({candidateTypeCounts.get(t) ?? 0})
	                          </option>
	                        ))}
	                      </select>
	                    </label>
	                    <div className="candidates-filter-meta">
	                      Showing {visibleCandidates.length} of {candidates.length}
	                    </div>
	                  </div>
	                  {itineraryData ? (
	                    <div className="activity-meta">
	                      Locks are applied when you click “Plan my day” again.
	                    </div>
	                  ) : null}
	                </div>
	                {visibleCandidates.map((c, idx) => (
	                  <CandidateRow
	                    key={`${c.xid ?? "noid"}-${idx}`}
	                    c={c}
	                    locked={Boolean(c.xid && lockedXids.has(c.xid))}
                    onToggleLock={toggleLock}
                  />
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
