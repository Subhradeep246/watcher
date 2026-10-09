"""Scan nearby NYC DOT cameras for the same tracked object (vision-model re-ID)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from ..schemas import Camera, CameraSighting, PathPrediction, VisionResult
from .camera_tracking import bearing_deg, nearest_cameras, top_direction

if TYPE_CHECKING:
    from ..agents.orchestrator import Orchestrator
    from .nyc_data import NYCDataService

_MAX_SCAN = 3
_MAX_NEARBY_M = 480.0
_MIN_CONF = 0.48


def _rank_nearby(
    origin: Camera,
    cameras: list[Camera],
    direction: Optional[str],
    *,
    max_scan: int = _MAX_SCAN,
) -> list[Camera]:
    nearby = nearest_cameras(
        cameras,
        origin.lat,
        origin.lng,
        exclude_id=origin.id,
        max_count=10,
        max_m=_MAX_NEARBY_M,
    )
    if not direction or not nearby:
        return nearby[:max_scan]

    dir_bearing = {
        "north": 0.0,
        "east": 90.0,
        "south": 180.0,
        "west": 270.0,
    }.get(direction)
    if dir_bearing is None:
        return nearby[:_MAX_SCAN]

    def score(cam: Camera) -> float:
        d = bearing_deg(origin.lat, origin.lng, cam.lat, cam.lng)
        delta = abs(d - dir_bearing)
        if delta > 180:
            delta = 360 - delta
        dist = ((cam.lat - origin.lat) ** 2 + (cam.lng - origin.lng) ** 2) ** 0.5
        return delta * 2 + dist * 1000

    nearby.sort(key=score)
    return nearby[:max_scan]


def sighting_from(cam: Camera, vision: VisionResult) -> CameraSighting:
    return CameraSighting(
        camera_id=cam.id,
        camera_name=cam.name,
        lat=cam.lat,
        lng=cam.lng,
        detected=vision.detected,
        confidence=vision.confidence,
        object_label=vision.object_label,
        bounding_box=vision.bounding_box,
    )


def paths_from_sightings(
    origin: Camera,
    sightings: list[CameraSighting],
) -> list[PathPrediction]:
    hits = [s for s in sightings if s.detected and s.confidence >= _MIN_CONF]
    if not hits:
        return [PathPrediction(direction="stop", probability=1.0)]

    if len(hits) == 1:
        return [PathPrediction(direction="stop", probability=0.55)]

    # Movement bearing from origin to farthest confident sighting.
    farthest = max(
        hits,
        key=lambda s: (s.lat - origin.lat) ** 2 + (s.lng - origin.lng) ** 2,
    )
    br = bearing_deg(origin.lat, origin.lng, farthest.lat, farthest.lng)
    if br >= 315 or br < 45:
        primary = "north"
    elif br < 135:
        primary = "east"
    elif br < 225:
        primary = "south"
    else:
        primary = "west"

    return [
        PathPrediction(direction=primary, probability=0.78),
        PathPrediction(direction="stop", probability=0.22),
    ]


def best_sighting(
    origin: Camera,
    sightings: list[CameraSighting],
) -> Optional[CameraSighting]:
    hits = [s for s in sightings if s.detected and s.confidence >= _MIN_CONF]
    if not hits:
        return None

    def score(s: CameraSighting) -> float:
        area = 1.0
        if s.bounding_box:
            area = s.bounding_box.width * s.bounding_box.height
        same = 1.15 if s.camera_id == origin.id else 1.0
        return s.confidence * area * same

    return max(hits, key=score)


def tracker_position(
    origin: Camera,
    sightings: list[CameraSighting],
) -> tuple[float, float]:
    hits = [s for s in sightings if s.detected and s.confidence >= _MIN_CONF]
    if not hits:
        return origin.lat, origin.lng
    if len(hits) == 1:
        return hits[0].lat, hits[0].lng

    wsum = sum(s.confidence for s in hits)
    lat = sum(s.lat * s.confidence for s in hits) / wsum
    lng = sum(s.lng * s.confidence for s in hits) / wsum
    return lat, lng


def _dedupe(cameras: list[Camera]) -> list[Camera]:
    seen: set[str] = set()
    out: list[Camera] = []
    for cam in cameras:
        if cam.id not in seen:
            seen.add(cam.id)
            out.append(cam)
    return out


def search_targets(
    origin: Camera,
    all_cameras: list[Camera],
    paths: list[PathPrediction],
    route_cam: Optional[Camera],
) -> list[Camera]:
    """Cameras to scan for the object: predicted-route first, then nearby."""
    direction = top_direction(paths)
    nearby = _rank_nearby(origin, all_cameras, direction)
    ordered: list[Camera] = []
    if route_cam and route_cam.id != origin.id:
        ordered.append(route_cam)
    ordered.extend(nearby)
    return _dedupe(ordered)[: _MAX_SCAN + 1]


async def scan_nearby_cameras(
    orch: Orchestrator,
    relocate_desc: str,
    origin: Camera,
    all_cameras: list[Camera],
    nyc: NYCDataService,
    paths: list[PathPrediction],
    primary: VisionResult,
    *,
    route_cam: Optional[Camera] = None,
    force: bool = False,
) -> list[CameraSighting]:
    """Vision-model re-ID across predicted-route + nearby feeds.

    When ``force`` is set the scan runs even if the object is no longer on the
    current feed — this is how we keep looking for it after it leaves frame.
    """
    sightings: list[CameraSighting] = [sighting_from(origin, primary)]
    if not primary.detected and not force:
        return sightings
    if not relocate_desc.strip():
        return sightings

    targets = search_targets(origin, all_cameras, paths, route_cam)
    if not targets:
        return sightings

    async def check(cam: Camera) -> CameraSighting:
        snap = await nyc.snapshot_data_uri(cam)
        vision, _ = await orch._vision_fast(
            relocate_desc,
            snap,
            seed_bbox=None,
            relocate=True,
        )
        return sighting_from(cam, vision)

    extra = await asyncio.gather(*[check(c) for c in targets])
    for s in extra:
        if s.detected and s.confidence >= _MIN_CONF:
            sightings.append(s)
    return sightings
