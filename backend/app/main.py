"""TheWatcher FastAPI backend."""
from __future__ import annotations

import httpx
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .agents.orchestrator import Orchestrator
from .config import get_settings
from .schemas import Camera, WatchRequest, WatchResponse
from .services.nyc_data import NYCDataService
from .services.routing import route_on_roads
from .tracing import init_tracing, tracing_enabled
from .telemetry import journal, request_id
from .services.incident_brief import build_brief

settings = get_settings()
app = FastAPI(title="TheWatcher API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

nyc = NYCDataService()
init_tracing()  # W&B Weave — before providers so their calls are traced
orchestrator = Orchestrator()


def _provider_info(p) -> dict:
    if p is None:
        return {"enabled": False, "model": "", "label": "none"}
    return {"enabled": p.enabled, "model": p.model, "label": p.label}


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "providers": {
            "primary": _provider_info(orchestrator.primary),
            "secondary": _provider_info(orchestrator.secondary),
        },
        "tracing": {
            "weave": tracing_enabled(),
            "project": settings.wandb_project if tracing_enabled() else "",
        },
        "data": {
            "ny511_live": bool(settings.ny511_api_key),
        },
        "snapshot_interval_ms": 2000,
    }


@app.get("/api/cameras", response_model=list[Camera])
async def list_cameras() -> list[Camera]:
    return await nyc.cameras()


@app.get("/api/telemetry")
async def telemetry() -> dict:
    return journal()


@app.get("/api/cameras/{camera_id}", response_model=Camera)
async def get_camera(camera_id: str) -> Camera:
    cam = await nyc.camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    return cam


@app.get("/api/cameras/{camera_id}/snapshot")
async def camera_snapshot(camera_id: str) -> Response:
    """Proxy the live NYC DOT JPEG (avoids CORS / lets us cache-bust)."""
    cam = await nyc.camera(camera_id)
    if not cam or not cam.image_url:
        raise HTTPException(status_code=404, detail="no snapshot for camera")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(cam.image_url)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="upstream snapshot unavailable") from None
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="upstream snapshot failed")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/watch", response_model=WatchResponse)
async def watch(req: WatchRequest) -> WatchResponse:
    cam = await nyc.camera(req.camera_id)
    if not cam:
        # allow factory/hospital modes with no real camera
        if req.mode == "nyc":
            raise HTTPException(status_code=404, detail="camera not found")
        cam = Camera(id=req.camera_id, name=f"{req.mode} feed", lat=0.0, lng=0.0)
    # Pull a live snapshot for the vision agent if the client didn't supply one.
    source = "sample" if cam.sample_image else "client_frame" if req.image_data_uri else "nyc_dot"
    if not req.image_data_uri:
        req.image_data_uri = await nyc.snapshot_data_uri(cam)
        source = "sample" if cam.sample_image else "nyc_dot"
    if not req.image_data_uri:
        source = "unavailable"
    incidents = await nyc.incidents_near(cam.lat, cam.lng)
    all_cams = await nyc.cameras()
    trace_id = uuid4().hex
    token = request_id.set(trace_id)
    try:
        result = await orchestrator.run(req, cam, incidents, all_cams, nyc)
        result.brief = build_brief(result, cam, source=source, image=req.image_data_uri,
                                  incidents=incidents, trace_id=trace_id)
        return result
    finally:
        request_id.reset(token)


@app.post("/api/track", response_model=WatchResponse)
async def track(req: WatchRequest) -> WatchResponse:
    """Primary vision tick; optional nearby search after an established track."""
    req.fast = True
    return await watch(req)


@app.get("/api/route")
async def road_route(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> dict:
    """Road-following polyline between two points (OSRM / OpenStreetMap)."""
    coords = await route_on_roads(from_lat, from_lng, to_lat, to_lng)
    return {"coordinates": coords}


# ---- Serve the built frontend (single-service deploy) -----------------
# Mounted LAST so it never shadows the /api routes above. In production the
# Docker build copies frontend/dist here; locally this dir may not exist
# (use the Vite dev server instead).
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles

_static_dir = Path(
    os.environ.get(
        "WATCHER_STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static")
    )
)
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.watcher_host,
        port=settings.watcher_port,
        reload=True,
    )


if __name__ == "__main__":
    run()
