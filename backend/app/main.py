"""Standalone DHMZ weather server: serves the JSON API and the static frontend.

Pure standard-library implementation (no FastAPI/uvicorn/starlette) - this app
never used any of their async/validation/DI features (every handler here was
already plain synchronous code underneath), so the extra dependency weight and
the ASGI server's background tick loop (which keeps a small but constant idle
CPU draw alive for the life of the process) weren't buying anything. Mirrors
the same http.server pattern already used by the eko-karta-zagreb-standalone
and stampar-pelud-standalone sibling apps.
"""
from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .dhmz_client import ICON_SYMBOL_RE, RadarStore, WeatherStore, fetch_icon

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dhmz")


def _find_frontend_dir() -> Path:
    # Docker image layout: /app/app/main.py, /app/frontend (see Dockerfile).
    # Local/dev layout: backend/app/main.py, <project root>/frontend.
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent / "frontend", here.parent.parent.parent / "frontend"):
        if candidate.is_dir():
            return candidate
    raise RuntimeError(f"Could not locate frontend/ directory near {here}")


FRONTEND_DIR = _find_frontend_dir()

weather_store = WeatherStore()
radar_store = RadarStore(get_location=weather_store.get_location)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _guess_content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    server_version = "DhmzWeatherStandalone/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_binary(self, status: int, data: bytes, content_type: str, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path

        try:
            if path == "/api/weather":
                try:
                    return self._send_json(HTTPStatus.OK, weather_store.get_weather())
                except RuntimeError as err:
                    return self._send_error_json(HTTPStatus.BAD_GATEWAY, str(err))

            if path == "/api/radar":
                try:
                    data, content_type = radar_store.get_radar()
                except Exception as err:  # noqa: BLE001 - upstream fetch/decode can fail in many ways
                    return self._send_error_json(HTTPStatus.BAD_GATEWAY, f"Radar image unavailable: {err}")
                return self._send_binary(
                    HTTPStatus.OK, data, content_type,
                    {"Cache-Control": f"max-age={config.RADAR_CACHE_SECONDS}"},
                )

            if path.startswith("/api/icon/"):
                # Proxied+cached same-origin instead of the frontend loading
                # https://meteo.hr/... icons directly: some Android kiosk
                # browsers/WebViews whitelist only their own configured
                # origin and silently block third-party image requests.
                symbol = path[len("/api/icon/"):].strip("/")
                if not ICON_SYMBOL_RE.match(symbol):
                    return self._send_error_json(HTTPStatus.BAD_REQUEST, f"Invalid icon symbol: {symbol!r}")
                data = fetch_icon(symbol)
                if data is None:
                    return self._send_error_json(HTTPStatus.BAD_GATEWAY, f"Icon unavailable: {symbol!r}")
                return self._send_binary(
                    HTTPStatus.OK, data, "image/svg+xml",
                    {"Cache-Control": "public, max-age=604800, immutable"},
                )

            if path == "/api/health":
                return self._send_json(HTTPStatus.OK, {"status": "ok"})

            if path.startswith("/api/"):
                return self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

            return self._serve_static(path)
        except Exception:  # noqa: BLE001 - last-resort safety net for a long-lived server
            logger.exception("Unhandled error handling %s", path)
            return self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"

        # Resolve safely inside FRONTEND_DIR, refusing any path-traversal attempt.
        rel = path.lstrip("/")
        target = (FRONTEND_DIR / rel).resolve()
        try:
            target.relative_to(FRONTEND_DIR.resolve())
        except ValueError:
            return self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden")

        if not target.is_file():
            return self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

        content_type = _guess_content_type(target)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if content_type.startswith("text/html") else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("DHMZ weather standalone server listening on port %s", port)
    logger.info("Serving static files from %s", FRONTEND_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
