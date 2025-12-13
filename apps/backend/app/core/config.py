import os

from dotenv import load_dotenv

load_dotenv()

# ---------- OpenTripMap ----------
OPENTRIPMAP_API_BASE = "https://api.opentripmap.com/0.1"
OPENTRIPMAP_LANG = "en"
OPENTRIPMAP_API_KEY = os.getenv("OPENTRIPMAP_API_KEY", "")

# ---------- Geoapify ----------
GEOAPIFY_API_BASE = "https://api.geoapify.com/v2"
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "")

# ---------- Google Places (New Places API v1) ----------
GOOGLE_PLACES_API_BASE = "https://places.googleapis.com/v1"
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# ---------- Common ----------
DEFAULT_SEARCH_RADIUS_M = int(os.getenv("DEFAULT_SEARCH_RADIUS_M", "12000"))
DEBUG = os.getenv("DEBUG", "0") not in {"0", "", "false", "False"}

# Which provider to try first: "google" | "geoapify" | "opentripmap"
PLACES_PRIMARY = os.getenv("PLACES_PRIMARY", "geoapify").lower()
# If true, try the secondary provider when the primary returns no results / raises
PLACES_FALLBACK = os.getenv("PLACES_FALLBACK", "1") not in {"0", "", "false", "False"}
