"""FastAPI app: serves the DHMZ JSON API and the static frontend."""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .dhmz_client import RadarStore, WeatherStore

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

app = FastAPI(title="DHMZ Standalone Weather")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

weather_store = WeatherStore()
radar_store = RadarStore(get_location=weather_store.get_location)


@app.get("/api/weather")
def api_weather():
    try:
        return weather_store.get_weather()
    except RuntimeError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@app.get("/api/radar")
def api_radar():
    try:
        data, content_type = radar_store.get_radar()
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Radar image unavailable: {err}") from err
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": f"max-age={config.RADAR_CACHE_SECONDS}"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
