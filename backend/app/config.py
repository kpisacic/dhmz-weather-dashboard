"""Runtime configuration, read from environment variables (see docker-compose.yml).

The four *_NAME values identify your station across four independent DHMZ
keyspaces (current conditions, forecast region, forecast text, 7-day
meteogram) - see the README for the full lists of valid values.
"""
import os


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


STATION_NAME = os.environ.get("DHMZ_STATION_NAME", "Zagreb-Maksimir")
FORECAST_REGION_NAME = os.environ.get("DHMZ_FORECAST_REGION_NAME", "Zagreb")
FORECAST_TEXT = os.environ.get("DHMZ_FORECAST_TEXT", "zg_text")
FORECAST_STATION_NAME = os.environ.get("DHMZ_FORECAST_STATION_NAME", "ZAGREB-MAKSIMIR")

# Optional manual override; normally latitude/longitude are read straight off
# the station's own entry in the current-conditions feed.
LATITUDE_OVERRIDE = os.environ.get("DHMZ_LATITUDE")
LONGITUDE_OVERRIDE = os.environ.get("DHMZ_LONGITUDE")

TIMEZONE = os.environ.get("DHMZ_TIMEZONE", "Europe/Zagreb")

WEATHER_CACHE_SECONDS = int(os.environ.get("DHMZ_WEATHER_CACHE_SECONDS", "1800"))
RADAR_CACHE_SECONDS = int(os.environ.get("DHMZ_RADAR_CACHE_SECONDS", "600"))

MARK_LOCATION = _bool("DHMZ_MARK_LOCATION", True)
RADAR_FORMAT = os.environ.get("DHMZ_RADAR_FORMAT", "WEBP").upper()  # WEBP or GIF

STATE_FILE = os.environ.get("DHMZ_STATE_FILE", "/data/state.json")
