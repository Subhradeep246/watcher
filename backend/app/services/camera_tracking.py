"""Geo helpers for multi-camera object handoff."""
from __future__ import annotations

import math
from typing import Optional

from ..schemas import Camera, PathPrediction, TrackerResult

# ~150 m step along predicted bearing when picking the next camera.
_HANDOFF_STEP_M = 150.0
_MAX_HANDOFF_M = 650.0
_MAX_BEARING_DELTA = 55.0

_DIR_BEARING: dict[str, float] = {
    "north": 0.0,
    "northeast": 45.0,
    "east": 90.0,
    "southeast": 135.0,
    "south": 180.0,
    "southwest": 225.0,
    "west": 270.0,
    "northwest": 315.0,
}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_delta(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def project(lat: float, lng: float, bearing: float, distance_m: float) -> tuple[float, float]:
    r = 6_371_000.0
    br = math.radians(bearing)
    p1 = math.radians(lat)
    lng1 = math.radians(lng)
    p2 = math.asin(
        math.sin(p1) * math.cos(distance_m / r)
        + math.cos(p1) * math.sin(distance_m / r) * math.cos(br)
    )
    lng2 = lng1 + math.atan2(
        math.sin(br) * math.sin(distance_m / r) * math.cos(p1),
        math.cos(distance_m / r) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), math.degrees(lng2)


def nearest_cameras(
    cameras: list[Camera],
    lat: float,
    lng: float,
    *,
    exclude_id: str | None = None,
    max_count: int = 6,
    max_m: float = 500.0,
) -> list[Camera]:
    ranked: list[tuple[float, Camera]] = []
    for cam in cameras:
        if exclude_id and cam.id == exclude_id:
            continue
        d = haversine_m(lat, lng, cam.lat, cam.lng)
        if d <= max_m:
            ranked.append((d, cam))
    ranked.sort(key=lambda x: x[0])
    return [c for _, c in ranked[:max_count]]


def bbox_score(box: dict) -> float:
    """Higher = object more visible / centered in frame."""
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])
    area = max(w, 1) * max(h, 1)
    cx, cy = x + w / 2, y + h / 2
    centrality = 1.0 - (abs(cx - 500) / 500 + abs(cy - 500) / 500) / 2
    edge_penalty = 1.0
    if x < 80 or y < 80 or x + w > 920 or y + h > 920:
        edge_penalty = 0.55
    return area * max(centrality, 0.1) * edge_penalty


def bbox_near_edge(box: dict) -> bool:
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])
    return x < 100 or y < 100 or x + w > 900 or y + h > 900


def bbox_center_drift(a: dict, b: dict) -> float:
    """Distance (0-1000 space) between the centers of two boxes."""
    acx = int(a["x"]) + int(a["width"]) / 2
    acy = int(a["y"]) + int(a["height"]) / 2
    bcx = int(b["x"]) + int(b["width"]) / 2
    bcy = int(b["y"]) + int(b["height"]) / 2
    return math.hypot(acx - bcx, acy - bcy)


def top_direction(paths: list[PathPrediction]) -> Optional[str]:
    movable = [p for p in paths if p.direction != "stop" and p.probability > 0.12]
    if not movable:
        return None
    return max(movable, key=lambda p: p.probability).direction


def direction_from_bbox(box: dict) -> Optional[str]:
    """Guess exit direction when object is near the frame edge."""
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])
    cx, cy = x + w / 2, y + h / 2
    margins = {
        "west": x,
        "east": 1000 - (x + w),
        "north": y,
        "south": 1000 - (y + h),
    }
    edge, margin = min(margins.items(), key=lambda kv: kv[1])
    if margin > 140:
        return None
    # Tie-break using centroid when multiple edges are close.
    if margin <= 80:
        return edge
    if cx < 350:
        return "west"
    if cx > 650:
        return "east"
    if cy < 350:
        return "north"
    if cy > 650:
        return "south"
    return edge


def synthetic_paths_from_bbox(box: dict) -> list[PathPrediction]:
    d = direction_from_bbox(box)
    if not d:
        return [PathPrediction(direction="stop", probability=0.6)]
    return [
        PathPrediction(direction=d, probability=0.72),
        PathPrediction(direction="stop", probability=0.28),
    ]


# Minimum center displacement (0-1000 space) between two frames to count as
# real movement rather than detector jitter.
_MOVE_MIN = 22.0


def motion_paths(prev: dict, cur: dict) -> list[PathPrediction]:
    """Predicted heading from the object's OBSERVED motion across two frames.

    ``prev`` is the previous frame's box, ``cur`` the current one. Screen→compass
    convention matches the rest of the app: +x→east, -x→west, +y→south, -y→north.
    This is the most grounded signal we have (actual displacement), and any
    resulting handoff is still confirmed by re-detecting the object downstream.
    """
    pcx = int(prev["x"]) + int(prev["width"]) / 2
    pcy = int(prev["y"]) + int(prev["height"]) / 2
    ccx = int(cur["x"]) + int(cur["width"]) / 2
    ccy = int(cur["y"]) + int(cur["height"]) / 2
    dx, dy = ccx - pcx, ccy - pcy
    speed = math.hypot(dx, dy)
    if speed < _MOVE_MIN:
        return [PathPrediction(direction="stop", probability=0.7)]

    horiz = "east" if dx > 0 else "west"
    vert = "south" if dy > 0 else "north"
    ax, ay = abs(dx), abs(dy)
    total = ax + ay or 1.0

    paths: list[PathPrediction] = []
    if ax >= ay:
        paths.append(PathPrediction(direction=horiz, probability=round(0.55 + 0.4 * ax / total, 2)))
        if ay / (ax + 1) > 0.4:
            paths.append(PathPrediction(direction=vert, probability=round(0.1 + 0.3 * ay / total, 2)))
    else:
        paths.append(PathPrediction(direction=vert, probability=round(0.55 + 0.4 * ay / total, 2)))
        if ax / (ay + 1) > 0.4:
            paths.append(PathPrediction(direction=horiz, probability=round(0.1 + 0.3 * ax / total, 2)))
    return paths


def heading_toward_edge(box: dict, direction: Optional[str]) -> bool:
    """True when the object sits in the outer third of the frame on its heading
    side — i.e. it is about to leave view, so we should look at the camera ahead.
    """
    if not direction or direction == "stop":
        return False
    cx = int(box["x"]) + int(box["width"]) / 2
    cy = int(box["y"]) + int(box["height"]) / 2
    if direction == "east":
        return cx > 660
    if direction == "west":
        return cx < 340
    if direction == "south":
        return cy > 660
    if direction == "north":
        return cy < 340
    return False


def pick_handoff_camera(
    current: Camera,
    tracker: TrackerResult,
    paths: list[PathPrediction],
    cameras: list[Camera],
) -> Optional[tuple[Camera, str]]:
    """Pick the next camera along the predicted travel bearing."""
    direction = top_direction(paths)
    if not direction:
        return None

    target_bearing = _DIR_BEARING.get(direction)
    if target_bearing is None:
        return None

    proj_lat, proj_lng = project(
        tracker.lat, tracker.lng, target_bearing, _HANDOFF_STEP_M
    )
    candidates = nearest_cameras(
        cameras,
        proj_lat,
        proj_lng,
        exclude_id=current.id,
        max_count=8,
        max_m=_MAX_HANDOFF_M,
    )
    if not candidates:
        return None

    best: Optional[tuple[float, Camera]] = None
    for cam in candidates:
        cam_bearing = bearing_deg(tracker.lat, tracker.lng, cam.lat, cam.lng)
        if _angle_delta(cam_bearing, target_bearing) > _MAX_BEARING_DELTA:
            continue
        d_proj = haversine_m(proj_lat, proj_lng, cam.lat, cam.lng)
        d_from = haversine_m(tracker.lat, tracker.lng, cam.lat, cam.lng)
        # Prefer cameras ahead on the route, not behind the current one.
        if d_from < 40:
            continue
        score = d_proj + d_from * 0.15
        if not best or score < best[0]:
            best = (score, cam)

    if not best:
        return None

    cam = best[1]
    reason = (
        f"Object likely moving {direction} — next view: {cam.name} "
        f"({int(haversine_m(current.lat, current.lng, cam.lat, cam.lng))}m away)"
    )
    return cam, reason
