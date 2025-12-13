export type GeocodeResult = {
    display_name: string;
    lat: string;
    lon: string;
};

export async function geocode(query: string): Promise<GeocodeResult[]> {
    const q = query.trim();
    if (!q) return [];

    const url =
        `https://nominatim.openstreetmap.org/search?format=json&limit=5&q=` +
        encodeURIComponent(q);

    const res = await fetch(url, {
        headers: {
            // Nominatim recommends identifying the app; for local dev this is OK.
            "Accept": "application/json",
        },
    });

    if (!res.ok) {
        throw new Error(`Geocode error ${res.status}`);
    }

    return res.json();
}
