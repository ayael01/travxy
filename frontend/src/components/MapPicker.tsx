import L, {
    type LatLngExpression,
    type Map as LeafletMap,
    type LeafletMouseEvent,
} from "leaflet";

import { useEffect, useMemo, useRef } from "react";
import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";

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

export default function MapPicker({ lon, lat, onChange, onMapReady }: Props) {
    const center: LatLngExpression = useMemo(() => [lat, lon], [lat, lon]);
    const mapRef = useRef<LeafletMap | null>(null);

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
            <Marker position={center} />
        </MapContainer>
    );
}
