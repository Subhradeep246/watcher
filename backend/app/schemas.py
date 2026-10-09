"""Pydantic schemas shared across agents and API routes."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---- Agent I/O ---------------------------------------------------------
class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class VisionResult(BaseModel):
    object_label: str
    object_class: Literal["person", "vehicle", "other"] = "other"
    detected: bool = True
    confidence: float = Field(default=0.0, ge=0, le=1)
    bounding_box: Optional[BoundingBox] = None
    context: str = ""
    # Rich visual signature for re-ID across cameras (clothing, stickers, colors…).
    appearance: Optional[str] = None
    # Plate text or the single most unique marker.
    identity_hint: Optional[str] = None


class Intersection(BaseModel):
    roads: list[str] = Field(default_factory=list)
    lanes: list[str] = Field(default_factory=list)


class TrackerResult(BaseModel):
    camera_id: str
    lat: float
    lng: float
    intersection: Intersection = Field(default_factory=Intersection)


class PathPrediction(BaseModel):
    direction: str
    probability: float = Field(ge=0, le=1)


class PredictionResult(BaseModel):
    paths: list[PathPrediction] = Field(default_factory=list)


class PathRisk(BaseModel):
    direction: str
    risk_score: float = Field(ge=0, le=1)
    reason: str = ""


class RiskResult(BaseModel):
    path_risks: list[PathRisk] = Field(default_factory=list)


# ---- Provider run metadata --------------------------------------------
class ModelRun(BaseModel):
    provider: Literal["primary", "secondary"]
    host: str = ""
    status_code: Optional[int] = None
    retries: int = 0
    model: str
    ok: bool
    mocked: bool = False
    latency_ms: int = 0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    raw_text: str = ""
    parsed: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class AgentComparison(BaseModel):
    """One agent step run on both providers, side by side."""
    agent: str
    primary: Optional[ModelRun] = None
    secondary: Optional[ModelRun] = None


# ---- API request / response -------------------------------------------
class WatchRequest(BaseModel):
    camera_id: str
    object_description: str
    bounding_box: Optional[BoundingBox] = None
    image_data_uri: Optional[str] = None
    mode: Literal["nyc", "factory", "hospital"] = "nyc"
    # Fast path: skip multi-camera scan + dual-model comparison (tracking loop).
    fast: bool = False
    skip_camera_scan: bool = False


class HandoffInfo(BaseModel):
    camera_id: str
    camera_name: str
    reason: str


class CameraSighting(BaseModel):
    """Object seen (or not) on a specific camera feed."""
    camera_id: str
    camera_name: str
    lat: float
    lng: float
    detected: bool
    confidence: float = 0.0
    object_label: str = ""
    bounding_box: Optional[BoundingBox] = None


class IncidentBrief(BaseModel):
    id: str
    created_at: str
    title: str
    priority: Literal["verify", "observe", "review"]
    frame_source: str
    frame_sha256: Optional[str] = None
    camera_name: str
    confidence: float
    summary: str
    actions: list[str]
    limitations: list[str]
    incident_context: list[str]
    risk_supported: bool
    risk_peak: Optional[float] = None
    evidence: list[str]


class WatchResponse(BaseModel):
    camera_id: str
    active_camera_id: str
    mode: str
    # Live tracking state: locked onto object / searching feeds / lost.
    status: Literal["tracking", "searching", "lost", "idle"] = "idle"
    # How many other feeds were scanned for the object this tick.
    searching_count: int = 0
    # The merged vision result from configured providers.
    vision: Optional[VisionResult] = None
    tracker: Optional[TrackerResult] = None
    prediction: Optional[PredictionResult] = None
    risk: Optional[RiskResult] = None
    # Suggested next camera when the object moves out of frame / along a path.
    handoff: Optional[HandoffInfo] = None
    # Same object matched on nearby camera feeds (appearance / color / plate).
    sightings: list[CameraSighting] = Field(default_factory=list)
    # Per-agent dual-run comparison for the UI.
    comparisons: list[AgentComparison] = Field(default_factory=list)
    # Human-readable control-room log lines.
    log: list[str] = Field(default_factory=list)
    brief: Optional[IncidentBrief] = None


class Camera(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    roads: list[str] = Field(default_factory=list)
    image_url: Optional[str] = None
    # bundled sample snapshot served by the backend
    sample_image: Optional[str] = None
