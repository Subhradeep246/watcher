"""Road-following routes via OSRM (OpenStreetMap street network)."""
from __future__ import annotations

import logging

import httpx

_log = logging.getLogger(__name__)
OSRM = "https://router.project-osrm.org"


async def snap_to_road(lat: float, lng: float) -> tuple[float, float]:
    """Snap a point to the nearest drivable road segment."""
    url = f"{OSRM}/nearest/v1/driving/{lng:.6f},{lat:.6f}"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, params={"number": 1})
        resp.raise_for_status()
        loc = resp.json()["waypoints"][0]["location"]
        return float(loc[1]), float(loc[0])
    except Exception as exc:  # noqa: BLE001
        _log.warning("OSRM snap failed: %s", exc)
        return lat, lng


async def route_on_roads(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> list[list[float]]:
    """Return [[lat, lng], ...] polyline that follows real streets."""
    snapped_from = await snap_to_road(from_lat, from_lng)
    snapped_to = await snap_to_road(to_lat, to_lng)
    coords = (
        f"{snapped_from[1]:.6f},{snapped_from[0]:.6f};"
        f"{snapped_to[1]:.6f},{snapped_to[0]:.6f}"
    )
    url = f"{OSRM}/route/v1/driving/{coords}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(data.get("message", "no route"))
        raw = data["routes"][0]["geometry"]["coordinates"]
        return [[pt[1], pt[0]] for pt in raw]
    except Exception as exc:  # noqa: BLE001
        _log.warning("OSRM route failed: %s", exc)
        return [
            [snapped_from[0], snapped_from[1]],
            [snapped_to[0], snapped_to[1]],
        ]
