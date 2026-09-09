"""DHMZ data fetching, parsing and caching.

Ported from the Home Assistant `dhmz` custom component (sensor.py / weather.py /
camera.py) - same feeds, same XPath queries, same condition/wind mapping tables,
same HTTPS-cert-fallback and stale-data resilience, minus everything that only
existed to satisfy Home Assistant's entity model.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Optional

import requests
from lxml import etree
from PIL import Image, ImageDraw, ImageSequence
from requests.exceptions import SSLError

from . import config

logger = logging.getLogger("dhmz")

CURRENT_SITUATION_API_URL = "https://vrijeme.hr/hrvatska_n.xml"
PRECIPITATION_API_URL = "https://vrijeme.hr/oborina.xml"
FORECAST_TODAY_API_URL = "https://prognoza.hr/prognoza_danas.xml"
FORECAST_TOMORROW_API_URL = "https://prognoza.hr/prognoza_sutra.xml"
FORECAST_7DAYS_API_URL = "https://meteo.hr/7d_graf_i_simboli.xml"
RADAR_ANIM_GIF_URL = "https://vrijeme.hr/anim_kompozit.gif"

USER_AGENT = "dhmz-standalone/1.0"

# DHMZ symbol code -> HA-style condition slug. Bare codes are daytime, an "n"
# suffix is the night variant (e.g. "1" = sunny, "1n" = clear-night).
CONDITION_CLASSES: dict[str, list[str]] = {
    "clear-night": ["1n"],
    "cloudy": ["5", "6", "5n", "6n"],
    "fog": ["7", "8", "9", "10", "11", "39", "40", "41", "42",
            "7n", "8n", "9n", "10n", "11n", "39n", "40n", "41n", "42n"],
    "hail": [],
    "lightning": ["15", "25", "29", "15n", "25n", "29n"],
    "lightning-rainy": ["16", "17", "18", "30", "31", "16n", "17n", "18n", "30n", "31n"],
    "partlycloudy": ["2", "3", "4", "2n", "3n", "4n"],
    "pouring": ["14", "28", "32", "14n", "28n", "32n"],
    "rainy": ["12", "13", "26", "27", "12n", "13n", "26n", "27n"],
    "snowy": ["22", "23", "24", "36", "37", "38", "22n", "23n", "24n", "36n", "37n", "38n"],
    "snowy-rainy": ["19", "20", "21", "33", "34", "35", "19n", "20n", "21n", "33n", "34n", "35n"],
    "sunny": ["1"],
    "windy": [],
    "windy-variant": [],
    "exceptional": ["-"],
}

# Trailing digit of the meteogram's <vjetar> string -> a discretized wind
# force bucket in km/h (not a real measurement, just DHMZ's own scale).
WIND_SPEED_MAPPING = {0: 0, 1: 10, 2: 25, 3: 50}

# Both feeds report wind bearing as an 8-point compass abbreviation (plus "C"
# for calm), not a numeric angle - despite the HA integration's SENSOR_TYPES
# table claiming degrees/int for this field, live data is always text like
# "SW". Degrees are derived from this for a smooth rotating arrow in the UI.
COMPASS_TO_DEGREES = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def format_condition(symbol: Optional[str]) -> str:
    """Map a DHMZ weather symbol code to an HA-style condition slug."""
    for condition, symbols in CONDITION_CLASSES.items():
        if symbol in symbols:
            return condition
    return "exceptional"


def compass_to_degrees(compass: Optional[str]) -> Optional[int]:
    return COMPASS_TO_DEGREES.get(compass) if compass else None


def icon_url(symbol: Optional[str]) -> Optional[str]:
    if not symbol or symbol == "-":
        return None
    return f"https://meteo.hr/assets/images/icons/{symbol}.svg"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_bytes(url: str, timeout: float = 10.0) -> bytes:
    """GET a URL, falling back to plain HTTP if the TLS certificate is invalid.

    DHMZ has periodically mis-served a certificate issued for the wrong host on
    vrijeme.hr. These feeds are public and unauthenticated, so retry over HTTP
    rather than losing all data until DHMZ fixes their certificate.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        return resp.content
    except SSLError as err:
        if not url.startswith("https://"):
            raise
        fallback_url = "http://" + url[len("https://"):]
        logger.warning("TLS error fetching %s (%s); retrying over %s", url, err, fallback_url)
        resp = requests.get(fallback_url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        return resp.content


def fetch_xml(url: str) -> Optional["etree._Element"]:
    try:
        raw = fetch_bytes(url)
    except (requests.RequestException, OSError) as err:
        logger.error("Failed to fetch %s: %s", url, err)
        return None
    try:
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as err:
        logger.error("Failed to parse XML from %s: %s", url, err)
        return None
    if root is None:
        logger.error("DHMZ served unparseable XML from %s", url)
    return root


def fetch_current_situation(station_name: str) -> Optional[dict[str, str]]:
    """Current conditions for one station, from hrvatska_n.xml + oborina.xml."""
    root = fetch_xml(CURRENT_SITUATION_API_URL)
    if root is None:
        return None

    podatci = root.xpath(f"//Hrvatska/Grad[GradIme='{station_name}']/Podatci/*")
    if not podatci:
        logger.error("Station %r not found in hrvatska_n.xml", station_name)
        return None

    data: dict[str, str] = {el.tag: (el.text or "").strip() for el in podatci}

    lat = root.xpath(f"//Hrvatska/Grad[GradIme='{station_name}']/Lat/text()")
    lon = root.xpath(f"//Hrvatska/Grad[GradIme='{station_name}']/Lon/text()")
    if lat:
        data["Lat"] = lat[0].strip()
    if lon:
        data["Lon"] = lon[0].strip()

    date_ = root.xpath("//Hrvatska/DatumTermin/Datum/text()")
    termin = root.xpath("//Hrvatska/DatumTermin/Termin/text()")
    if date_ and termin:
        data["Timestamp"] = f"{date_[0]} {termin[0]}:00:00"

    precip_root = fetch_xml(PRECIPITATION_API_URL)
    if precip_root is not None:
        kolicina = precip_root.xpath(f"//dnevna_oborina/grad[ime='{station_name}']/kolicina/text()")
        if kolicina:
            data["kolicina"] = kolicina[0].strip()
            pdate = precip_root.xpath("//dnevna_oborina/datumtermin/datum/text()")
            ptermin = precip_root.xpath("//dnevna_oborina/datumtermin/termin/text()")
            if pdate and ptermin:
                data["kolicina_timestamp"] = f"{pdate[0]} {ptermin[0]}:00:00"

    return data


def fetch_forecast_text(url: str, text_key: str) -> Optional[str]:
    """Long-form Croatian forecast text (today/tomorrow) for the configured region."""
    root = fetch_xml(url)
    if root is None:
        return None
    values = root.xpath(f"//VW/section/param[@name='{text_key}']/@value")
    if not values:
        logger.error("Forecast text key %r not found in %s", text_key, url)
        return None
    return str(values[0])


def fetch_forecast_hourly(forecast_station_name: str) -> Optional[list[dict[str, Any]]]:
    """Hourly forecast points (several days out) from the 7-day meteogram feed."""
    root = fetch_xml(FORECAST_7DAYS_API_URL)
    if root is None:
        return None

    nodes = root.xpath(f"//sedamdana/grad[@code='{forecast_station_name}']/*")
    if not nodes:
        logger.error("Forecast station %r not found in 7d_graf_i_simboli.xml", forecast_station_name)
        return None

    result: list[dict[str, Any]] = []
    for node in nodes:
        try:
            symbol = node.xpath("simbol/text()")[0]
            tmax = node.xpath("t_2m/text()")[0]
            wind = node.xpath("vjetar/text()")[0]
            precip = node.xpath("oborina/text()")[0]
            when = datetime.strptime(f"{node.get('datum')} {node.get('sat')}", "%d.%m.%Y. %H")
        except (IndexError, TypeError, ValueError):
            continue
        result.append({
            "datetime": when,
            "temperature": _safe_float(tmax),
            "precipitation": _safe_float(precip) or 0.0,
            "wind_speed": WIND_SPEED_MAPPING.get(int(wind[-1:]), 0) if wind and wind[-1:].isdigit() else None,
            "wind_bearing": wind[:-1] if wind else None,
            "condition": format_condition(symbol),
            "weather_symbol": symbol,
        })
    result.sort(key=lambda e: e["datetime"])
    return result


class WeatherStore:
    """Fetches, caches and assembles the full weather snapshot the frontend polls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Optional[dict[str, Any]] = None
        self._cache_time: float = 0.0
        self._last_good_symbol: Optional[str] = None
        self._load_state()

    def _load_state(self) -> None:
        try:
            with open(config.STATE_FILE, encoding="utf-8") as fh:
                self._last_good_symbol = json.load(fh).get("last_good_symbol")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
            with open(config.STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump({"last_good_symbol": self._last_good_symbol}, fh)
        except OSError as err:
            logger.warning("Could not persist state to %s: %s", config.STATE_FILE, err)

    def get_location(self) -> Optional[tuple[float, float]]:
        """Latest known (lat, lon) for the configured station, for sun times / radar marker."""
        if config.LATITUDE_OVERRIDE and config.LONGITUDE_OVERRIDE:
            return float(config.LATITUDE_OVERRIDE), float(config.LONGITUDE_OVERRIDE)
        if self._cache and self._cache.get("latitude") is not None and self._cache.get("longitude") is not None:
            return self._cache["latitude"], self._cache["longitude"]
        return None

    def get_weather(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            age = time.time() - self._cache_time
            if self._cache is not None and not force and age < config.WEATHER_CACHE_SECONDS:
                return self._cache

            fresh = self._build_weather()
            if fresh is not None:
                self._cache = fresh
                self._cache_time = time.time()
            if self._cache is None:
                raise RuntimeError("Unable to fetch DHMZ data and no cached data is available yet")
            return self._cache

    def _build_weather(self) -> Optional[dict[str, Any]]:
        current = fetch_current_situation(config.STATION_NAME)
        today_text = fetch_forecast_text(FORECAST_TODAY_API_URL, config.FORECAST_TEXT)
        tomorrow_text = fetch_forecast_text(FORECAST_TOMORROW_API_URL, config.FORECAST_TEXT)
        hourly = fetch_forecast_hourly(config.FORECAST_STATION_NAME)

        if current is None and today_text is None and tomorrow_text is None and hourly is None:
            return None  # total fetch failure; caller keeps whatever is cached

        base: dict[str, Any] = dict(self._cache) if self._cache else {}

        if current:
            raw_symbol = current.get("VrijemeZnak")
            if raw_symbol and format_condition(raw_symbol) != "exceptional":
                self._last_good_symbol = raw_symbol
                self._save_state()
            effective_symbol = (
                raw_symbol if (raw_symbol and format_condition(raw_symbol) != "exceptional")
                else (self._last_good_symbol or raw_symbol)
            )

            wind_bearing_compass = current.get("VjetarSmjer")
            base.update({
                "station": current.get("GradIme", config.STATION_NAME),
                "updated": current.get("Timestamp"),
                "temperature": _safe_float(current.get("Temp")),
                "humidity": _safe_float(current.get("Vlaga")),
                "pressure": _safe_float(current.get("Tlak")),
                "pressure_tendency": _safe_float(current.get("TlakTend")),
                "wind_speed": _safe_float(current.get("VjetarBrzina")),
                "wind_bearing_compass": wind_bearing_compass,
                "wind_bearing_deg": compass_to_degrees(wind_bearing_compass),
                "precipitation": _safe_float(current.get("kolicina")) or 0.0,
                "precipitation_updated": current.get("kolicina_timestamp"),
                "condition_text": current.get("Vrijeme"),
                "weather_symbol": effective_symbol,
                "condition": format_condition(effective_symbol),
                "icon_url": icon_url(effective_symbol),
                "latitude": _safe_float(current.get("Lat")),
                "longitude": _safe_float(current.get("Lon")),
            })

        if today_text is not None:
            base["forecast_today"] = today_text
        if tomorrow_text is not None:
            base["forecast_tomorrow"] = tomorrow_text

        if hourly:
            now = datetime.now()
            future = [h for h in hourly if h["datetime"] > now]
            base["forecast_list"] = [
                {
                    **{k: v for k, v in h.items() if k != "datetime"},
                    "datetime": h["datetime"].isoformat(),
                    "icon_url": icon_url(h["weather_symbol"]),
                }
                for h in future
            ]

        lat, lon = base.get("latitude"), base.get("longitude")
        if lat is not None and lon is not None:
            try:
                base["sun"] = compute_sun_times(lat, lon)
            except Exception as err:  # astral edge cases (polar day/night) shouldn't break the response
                logger.warning("Could not compute sun times: %s", err)

        base["station_name"] = config.STATION_NAME
        base["fetched_at"] = datetime.utcnow().isoformat() + "Z"
        return base


def compute_sun_times(lat: float, lon: float) -> dict[str, str]:
    from astral import LocationInfo
    from astral.sun import sun
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(config.TIMEZONE)
    loc = LocationInfo(latitude=lat, longitude=lon, timezone=config.TIMEZONE)
    times = sun(loc.observer, date=datetime.now(tz).date(), tzinfo=tz)
    return {
        "sunrise": times["sunrise"].isoformat(),
        "sunset": times["sunset"].isoformat(),
    }


def _sniff_content_type(data: bytes) -> str:
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/jpeg"


def _convert_to_webp(raw: bytes) -> bytes:
    try:
        im = Image.open(BytesIO(raw))
        frames, durations = [], []
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.copy().convert("RGBA"))
            durations.append(frame.info.get("duration", 100))
        out = BytesIO()
        frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:],
                        optimize=True, duration=durations, loop=0)
        return out.getvalue()
    except Exception as err:
        logger.error("Failed to convert radar GIF to WebP: %s", err)
        return raw


def _draw_location_marker(raw: bytes, lat: float, lon: float, fmt: str) -> bytes:
    """Overlay a red dot at (lat, lon) on every frame of the radar composite.

    The pixel-mapping formula is a hand-fit calibration of this specific
    composite image's geographic bounding box, ported as-is from camera.py.
    """
    try:
        im = Image.open(BytesIO(raw))
        frames, durations = [], []
        for frame in ImageSequence.Iterator(im):
            frame_rgba = frame.copy().convert("RGBA")
            draw = ImageDraw.Draw(frame_rgba)
            x = int((0.11346541830650277 * lon - 1.3351816168381) * float(im.size[0]))
            y = int((-0.15304197356993342 * lat + 7.31403749212996) * float(im.size[1]))
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 0, 0), outline=(0, 0, 0))
            frames.append(frame_rgba)
            durations.append(frame.info.get("duration", 100))
        out = BytesIO()
        frames[0].save(out, format=fmt, save_all=True, append_images=frames[1:],
                        optimize=True, duration=durations, loop=0)
        return out.getvalue()
    except Exception as err:
        logger.error("Failed to draw location marker on radar image: %s", err)
        return raw


class RadarStore:
    """Fetches and caches the animated radar composite, with optional location marker."""

    def __init__(self, get_location: Callable[[], Optional[tuple[float, float]]]) -> None:
        self._lock = threading.Lock()
        self._get_location = get_location
        self._bytes: Optional[bytes] = None
        self._content_type: str = "image/webp"
        self._fetched_at: float = 0.0

    def get_radar(self, force: bool = False) -> tuple[bytes, str]:
        with self._lock:
            age = time.time() - self._fetched_at
            if self._bytes is not None and not force and age < config.RADAR_CACHE_SECONDS:
                return self._bytes, self._content_type

            try:
                raw = fetch_bytes(RADAR_ANIM_GIF_URL, timeout=15)
            except (requests.RequestException, OSError) as err:
                logger.error("Failed to fetch radar image: %s", err)
                if self._bytes is not None:
                    return self._bytes, self._content_type
                raise

            processed, content_type = self._process(raw)
            self._bytes, self._content_type, self._fetched_at = processed, content_type, time.time()
            return self._bytes, self._content_type

    def _process(self, raw: bytes) -> tuple[bytes, str]:
        location = self._get_location() if config.MARK_LOCATION else None
        if location:
            fmt = "GIF" if config.RADAR_FORMAT != "WEBP" else "WEBP"
            data = _draw_location_marker(raw, location[0], location[1], fmt)
            return data, ("image/webp" if fmt == "WEBP" else "image/gif")
        if config.RADAR_FORMAT == "WEBP":
            return _convert_to_webp(raw), "image/webp"
        return raw, _sniff_content_type(raw)
