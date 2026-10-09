"""NYC camera + alert data.

Camera source priority:
  1. NYC DOT public API (webcams.nyctmc.org) — no key, ~950 live cameras.
  2. 511NY API (if NY511_API_KEY set).
  3. Bundled sample cameras (fully offline fallback).
We fetch the camera list server-side to avoid the browser CORS block, and the
live JPEG snapshots are base64'd here so the vision model can consume them.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Optional

import httpx

from ..config import get_settings
from ..schemas import Camera

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_NYCTMC_BASE = "https://webcams.nyctmc.org/api/cameras"


def _load_sample_cameras() -> list[Camera]:
    raw = json.loads((_DATA_DIR / "sample_cameras.json").read_text(encoding="utf-8"))
    cams = []
    for c in raw:
        # Generate a placeholder snapshot (SVG data URI) so the UI always has
        # an image to show even with no live feed / no API key.
        c["sample_image"] = _placeholder_snapshot(c["name"])
        cams.append(Camera(**c))
    return cams


def _placeholder_snapshot(label: str) -> str:
    """Render a simple raster (PNG) street scene so the vision model gets a
    valid image for an explicitly labelled sample camera. Providers accept PNG.
    """
    import base64
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), (27, 34, 51))
    d = ImageDraw.Draw(img)
    # road
    d.rectangle([0, 300, 640, 480], fill=(42, 53, 80))
    # center cross-street
    d.rectangle([280, 300, 360, 480], fill=(57, 70, 107))
    # lane dashes
    for y in range(300, 480, 32):
        d.line([(320, y), (320, y + 18)], fill=(201, 212, 240), width=4)
    # a yellow "taxi"
    d.rectangle([158, 338, 202, 382], fill=(244, 196, 48))
    d.rectangle([166, 346, 194, 366], fill=(60, 60, 60))
    # labels
    d.text((20, 24), f"CAM - {label}", fill=(159, 179, 217))
    d.text((20, 44), "sample snapshot (no live key)", fill=(94, 108, 138))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class NYCDataService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._cameras: list[Camera] = []

    async def cameras(self) -> list[Camera]:
        if self._cameras:
            return self._cameras
        # 1. NYC DOT public API (no key)
        try:
            cams = await self._fetch_nyctmc_cameras()
            if cams:
                self._cameras = cams
                return self._cameras
        except Exception:  # noqa: BLE001 — fall through
            pass
        # 2. 511NY (if key)
        if self.settings.ny511_api_key:
            try:
                self._cameras = await self._fetch_live_cameras()
                return self._cameras
            except Exception:  # noqa: BLE001
                pass
        # 3. bundled samples — NOT cached, so we retry the live source next call
        return _load_sample_cameras()

    async def camera(self, camera_id: str) -> Optional[Camera]:
        for c in await self.cameras():
            if c.id == camera_id:
                return c
        return None

    async def _fetch_nyctmc_cameras(self) -> list[Camera]:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(_NYCTMC_BASE)
        resp.raise_for_status()
        out: list[Camera] = []
        for it in resp.json():
            try:
                if str(it.get("isOnline", "")).lower() != "true":
                    continue
                name = it.get("name", "NYC DOT Camera")
                cam_id = str(it["id"])
                out.append(
                    Camera(
                        id=cam_id,
                        name=name,
                        lat=float(it["latitude"]),
                        lng=float(it["longitude"]),
                        # split "A Ave @ B St" into individual roads for the tracker
                        roads=[s.strip() for s in re.split(r"[@&/]", name) if s.strip()],
                        image_url=it.get("imageUrl") or f"{_NYCTMC_BASE}/{cam_id}/image",
                        sample_image=None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def snapshot_data_uri(self, camera: Camera) -> Optional[str]:
        """Fetch a live JPEG and return it as a base64 data URI for the vision
        model. Falls back to any bundled sample image, then a drawn placeholder.
        """
        if camera.image_url:
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                    r = await client.get(camera.image_url)
                r.raise_for_status()
                ct = r.headers.get("content-type", "image/jpeg").split(";")[0]
                b64 = base64.b64encode(r.content).decode("ascii")
                return f"data:{ct};base64,{b64}"
            except Exception:  # noqa: BLE001
                pass
        return camera.sample_image

    async def _fetch_live_cameras(self) -> list[Camera]:
        url = "https://511ny.org/api/getcameras"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, params={"key": self.settings.ny511_api_key, "format": "json"}
            )
        resp.raise_for_status()
        out: list[Camera] = []
        for item in resp.json():
            try:
                out.append(
                    Camera(
                        id=str(item.get("ID") or item.get("Id")),
                        name=item.get("Name", "NYC Camera"),
                        lat=float(item["Latitude"]),
                        lng=float(item["Longitude"]),
                        roads=[r for r in [item.get("RoadwayName")] if r],
                        image_url=item.get("Url"),
                        sample_image=_placeholder_snapshot(item.get("Name", "CAM")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        # keep it manageable for the demo
        return out[:500]

    async def incidents_near(self, lat: float, lng: float, radius_km: float = 2.0) -> list[str]:
        """Aggregate incidents from 511NY live alerts + NYC Open Data crashes.

        Falls back to sample alerts if neither source is configured / returns data.
        """
        incidents: list[str] = []
        if self.settings.ny511_api_key:
            incidents += await self._fetch_511_alerts(lat, lng)
        if self.settings.socrata_app_token:
            incidents += await self._fetch_crash_history(lat, lng)

        if not incidents:
            return [
                "Roadwork: lane closure reported nearby (sample)",
                "Elevated crash density in past 12 months (sample)",
            ]
        return incidents[:8]

    async def _fetch_511_alerts(self, lat: float, lng: float) -> list[str]:
        try:
            url = "https://511ny.org/api/getalerts"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    url,
                    params={"key": self.settings.ny511_api_key, "format": "json"},
                )
            resp.raise_for_status()
            alerts = []
            for a in resp.json():
                try:
                    alat = float(a.get("Latitude"))
                    alng = float(a.get("Longitude"))
                except (TypeError, ValueError):
                    continue
                if abs(alat - lat) < 0.03 and abs(alng - lng) < 0.03:
                    desc = a.get("EventDescription") or a.get("Event") or "alert"
                    alerts.append(f"511NY alert: {desc}")
            return alerts[:5]
        except Exception:  # noqa: BLE001
            return []

    async def _fetch_crash_history(self, lat: float, lng: float) -> list[str]:
        """NYPD Motor Vehicle Collisions (dataset h9gi-nx95) near the camera."""
        d = 0.003  # ~300m bounding box
        where = (
            f"latitude > {lat - d} AND latitude < {lat + d} AND "
            f"longitude > {lng - d} AND longitude < {lng + d}"
        )
        select = (
            "count(*) as crashes, "
            "sum(number_of_persons_injured) as injured, "
            "sum(number_of_persons_killed) as killed"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    "https://data.cityofnewyork.us/resource/h9gi-nx95.json",
                    params={"$select": select, "$where": where},
                    headers={"X-App-Token": self.settings.socrata_app_token},
                )
            resp.raise_for_status()
            rows = resp.json()
            agg = rows[0] if rows else {}
            crashes = int(float(agg.get("crashes", 0) or 0))
            if crashes == 0:
                return ["NYC Open Data: no crashes on record at this corner"]
            injured = int(float(agg.get("injured", 0) or 0))
            killed = int(float(agg.get("killed", 0) or 0))
            return [
                f"NYC Open Data: {crashes} historical crashes within ~300m "
                f"({injured} injured, {killed} killed)"
            ]
        except Exception:  # noqa: BLE001
            return []
