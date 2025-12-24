import L, {
    type LatLngExpression,
    type Map as LeafletMap,
    type LeafletMouseEvent,
} from "leaflet";

import { useEffect, useMemo, useRef } from "react";
import { Circle, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { PlannedActivity } from "../types";

// Fix default marker icons (Vite + Leaflet quirk)
import marker2x from "leaflet/dist/images/marker-icon-2x.png";
import marker from "leaflet/dist/images/marker-icon.png";
import shadow from "leaflet/dist/images/marker-shadow.png";

// Configure default icon once
const DefaultIcon = L.icon({
    iconUrl: marker,
    iconRetinaUrl: marker2x,
    shadowUrl: shadow,
    iconSize: [25, 41],
    iconAnchor: [12, 41],
});
L.Marker.prototype.options.icon = DefaultIcon;

type Props = {
    lon: number;
    lat: number;
    radiusM?: number;
    itinerary?: PlannedActivity[];
    itineraryOrigin?: { lon: number; lat: number };
    onChange: (lon: number, lat: number) => void;
    onMapReady?: (map: LeafletMap) => void;
};

function ClickHandler({ onChange }: { onChange: (lon: number, lat: number) => void }) {
    useMapEvents({
        click(e: LeafletMouseEvent) {
            // Leaflet gives lat,lng - our API expects [lon, lat]
            onChange(e.latlng.lng, e.latlng.lat);
        },
    });
    return null;
}

function formatRadius(radiusM: number): string {
    if (!Number.isFinite(radiusM) || radiusM <= 0) return "";
    if (radiusM % 1000 === 0) return `${radiusM / 1000} km`;
    return `${radiusM} m`;
}

function numberedIcon(n: number) {
    return L.divIcon({
        className: "itinerary-marker",
        html: `<div class="itinerary-marker-icon"><span>${n}</span></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
    });
}

function FitToItinerary({
    boundsKey,
    bounds,
}: {
    boundsKey: string;
    bounds: L.LatLngBounds | null;
}) {
    const map = useMap();
    useEffect(() => {
        if (!bounds) return;
        map.fitBounds(bounds, { padding: [32, 32] });
    }, [map, boundsKey, bounds]);
    return null;
}

export default function MapPicker({
    lon,
    lat,
    radiusM,
    itinerary,
    itineraryOrigin,
    onChange,
    onMapReady,
}: Props) {
    const center: LatLngExpression = useMemo(() => [lat, lon], [lat, lon]);
    const mapRef = useRef<LeafletMap | null>(null);
    const showRadius = typeof radiusM === "number" && Number.isFinite(radiusM) && radiusM > 0;
    const stops = useMemo(() => {
        const acts = itinerary ?? [];
        return acts
            .map((a) => {
                const [stopLon, stopLat] = a.location;
                return {
                    xid: a.xid ?? `${stopLon},${stopLat}`,
                    name: a.name,
                    type: a.type,
                    segment: a.segment,
                    start_time: a.start_time,
                    end_time: a.end_time,
                    lon: stopLon,
                    lat: stopLat,
                };
            })
            .filter((s) => Number.isFinite(s.lon) && Number.isFinite(s.lat));
    }, [itinerary]);

    const itineraryBounds = useMemo(() => {
        if (stops.length === 0) return null;
        const o = itineraryOrigin
            ? ([itineraryOrigin.lat, itineraryOrigin.lon] as [number, number])
            : ([lat, lon] as [number, number]);
        const pts: L.LatLngExpression[] = [
            o,
            ...stops.map((s) => [s.lat, s.lon] as [number, number]),
        ];
        return L.latLngBounds(pts);
    }, [lat, lon, stops, itineraryOrigin]);

    const boundsKey = useMemo(() => {
        if (!itineraryBounds) return "";
        const o = itineraryOrigin
            ? { lat: itineraryOrigin.lat, lon: itineraryOrigin.lon }
            : { lat, lon };
        return [
            o.lat.toFixed(6),
            o.lon.toFixed(6),
            ...stops.map((s) => `${s.xid}:${s.lat.toFixed(6)},${s.lon.toFixed(6)}`),
        ].join("|");
    }, [lat, lon, stops, itineraryBounds, itineraryOrigin]);

    useEffect(() => {
        if (mapRef.current) {
            mapRef.current.setView(center);
        }
    }, [center]);

    useEffect(() => {
        if (mapRef.current && onMapReady) {
            onMapReady(mapRef.current);
        }
    }, [onMapReady]);

    const linePositions: LatLngExpression[] = useMemo(() => {
        if (stops.length === 0) return [];
        const o: LatLngExpression = itineraryOrigin
            ? ([itineraryOrigin.lat, itineraryOrigin.lon] as [number, number])
            : center;
        return [o, ...stops.map((s) => [s.lat, s.lon] as [number, number])];
    }, [center, stops, itineraryOrigin]);

    return (
        <MapContainer
            ref={mapRef}
            center={center}
            zoom={13}
            style={{ height: 520, width: "100%" }}
        >
            <TileLayer
                attribution="&copy; OpenStreetMap"
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <ClickHandler onChange={onChange} />
            {itineraryBounds ? <FitToItinerary boundsKey={boundsKey} bounds={itineraryBounds} /> : null}
            {showRadius ? (
                <Circle
                    center={center}
                    radius={radiusM}
                    pathOptions={{
                        color: "#0ea5e9",
                        weight: 2,
                        dashArray: "6 8",
                        fillColor: "#0ea5e9",
                        fillOpacity: 0.08,
                    }}
                >
                    <Tooltip sticky>Search radius: {formatRadius(radiusM)}</Tooltip>
                </Circle>
            ) : null}
            {linePositions.length >= 2 ? (
                <Polyline
                    positions={linePositions}
                    pathOptions={{ color: "#22c55e", weight: 4, opacity: 0.9 }}
                />
            ) : null}
            {stops.map((s, idx) => {
                const t =
                    s.start_time && s.end_time ? `${s.start_time}–${s.end_time}` : undefined;
                return (
                    <Marker
                        key={`${s.xid}-${idx}`}
                        position={[s.lat, s.lon]}
                        icon={numberedIcon(idx + 1)}
                    >
                        <Tooltip direction="top" offset={[0, -8]} opacity={0.95} sticky>
                            <div style={{ fontWeight: 600 }}>{s.name}</div>
                            <div style={{ fontSize: 12, opacity: 0.9 }}>
                                {s.type}
                                {s.segment ? ` • ${s.segment}` : ""}
                                {t ? ` • ${t}` : ""}
                            </div>
                        </Tooltip>
                    </Marker>
                );
            })}
            <Marker position={center} />
        </MapContainer>
    );
}
