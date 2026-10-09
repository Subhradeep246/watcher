"""Runs the 4-agent pipeline — dual-model vision consensus + fast Primary tracking."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..providers.base import LLMProvider
from ..providers.openai_compat import build_primary, build_secondary
from ..schemas import (
    AgentComparison,
    BoundingBox,
    Camera,
    HandoffInfo,
    Intersection,
    ModelRun,
    PathPrediction,
    PathRisk,
    PredictionResult,
    RiskResult,
    TrackerResult,
    VisionResult,
    WatchRequest,
    WatchResponse,
)
from ..services.camera_tracking import (
    bbox_center_drift,
    bbox_near_edge,
    bbox_score,
    heading_toward_edge,
    motion_paths,
    nearest_cameras,
    pick_handoff_camera,
    synthetic_paths_from_bbox,
)
from ..services.detection import (
    infer_target_class,
    merge_vision,
    sanitize_appearance,
    sanitize_identity_hint,
    sanitize_label,
    track_description,
)
from ..services.nyc_data import NYCDataService
from . import prompts
from ..tracing import traced
from ..services.multi_camera_track import (
    best_sighting,
    paths_from_sightings,
    scan_nearby_cameras,
    search_targets,
    tracker_position,
)

# Confidence needed on another feed before we switch the active camera to it.
_HANDOFF_COMMIT_CONF = 0.55
# Large bbox jump + low confidence on a continue frame ⇒ probably a different
# object (e.g. the model latched onto the other car/person in frame).
_MAX_TRACK_DRIFT = 420.0


class Orchestrator:
    def __init__(self) -> None:
        # Slot names kept for the UI wire format: "primary" = primary model
        # (NVIDIA NIM by default), "secondary" = comparison model (W&B Inference).
        self.primary: LLMProvider = build_primary()
        self.secondary: Optional[LLMProvider] = build_secondary()
        self._secondary_on = bool(self.secondary and self.secondary.enabled)

    async def _call_primary(
        self,
        system: str,
        user: str,
        image_data_uri: Optional[str] = None,
    ) -> ModelRun:
        return await self.primary.run(
            system=system, user=user, image_data_uri=image_data_uri
        )

    async def _run_agent(
        self,
        agent: str,
        system: str,
        user: str,
        image_data_uri: Optional[str] = None,
        *,
        compare: bool,
    ) -> AgentComparison:
        if compare and self.secondary is not None:
            primary_run, secondary_run = await asyncio.gather(
                self.primary.run(system=system, user=user, image_data_uri=image_data_uri, agent=agent),
                self.secondary.run(system=system, user=user, image_data_uri=image_data_uri, agent=agent),
            )
            return AgentComparison(agent=agent, primary=primary_run, secondary=secondary_run)
        run = await self.primary.run(system=system, user=user, image_data_uri=image_data_uri, agent=agent)
        return AgentComparison(agent=agent, primary=run, secondary=None)

    async def _vision_consensus(
        self,
        description: str,
        mode: str,
        image_data_uri: Optional[str],
        seed_bbox: Optional[BoundingBox] = None,
    ) -> tuple[VisionResult, AgentComparison]:
        target = infer_target_class(description)
        if not image_data_uri:
            return VisionResult(object_label=description, detected=False, context="Frame unavailable"), AgentComparison(agent="vision")
        system = prompts.vision_system(target)
        user = prompts.vision_user(description, mode, target, seed_bbox=seed_bbox)

        if self._secondary_on:
            primary_run, secondary_run = await asyncio.gather(
                self._call_primary(system, user, image_data_uri),
                self.secondary.run(system=system, user=user, image_data_uri=image_data_uri),
            )
        else:
            primary_run = await self._call_primary(system, user, image_data_uri)
            secondary_run = None

        primary_v = _safe(VisionResult, primary_run.parsed if primary_run.ok else None)
        secondary_v = (
            _safe(VisionResult, secondary_run.parsed if secondary_run and secondary_run.ok else None)
            if secondary_run
            else None
        )
        merged = merge_vision(primary_v, secondary_v, target, description, seed_bbox)
        if primary_v is None and secondary_v is None:
            merged.context = "Detection unavailable: no validated vision model output."
        cmp = AgentComparison(agent="vision", primary=primary_run, secondary=secondary_run)
        return merged, cmp

    async def _resolve_camera(
        self,
        req: WatchRequest,
        origin: Camera,
        all_cameras: list[Camera],
        nyc: NYCDataService,
        log: list[str],
    ) -> Camera:
        if req.mode != "nyc" or len(all_cameras) < 2:
            return origin

        target = infer_target_class(req.object_description)
        nearby = nearest_cameras(
            all_cameras, origin.lat, origin.lng, max_count=3, max_m=400.0
        )
        candidates: list[Camera] = [origin]
        seen = {origin.id}
        for cam in nearby:
            if cam.id not in seen:
                seen.add(cam.id)
                candidates.append(cam)

        best_cam = origin
        best_score = -1.0

        for cam in candidates:
            snap = await nyc.snapshot_data_uri(cam)
            system = prompts.vision_system(target)
            user = prompts.vision_user(req.object_description, req.mode, target)
            run = await self._call_primary(system, user, snap)
            v = _safe(VisionResult, run.parsed if run.ok else None)
            if not v or not v.detected or not v.bounding_box:
                continue
            score = bbox_score(v.bounding_box.model_dump()) * _norm_conf(v)
            if score > best_score:
                best_score = score
                best_cam = cam

        if best_cam.id != origin.id:
            log.append(f"Camera Router: switched to {best_cam.name}")
        return best_cam

    @staticmethod
    def _primary(cmp: AgentComparison) -> Optional[dict[str, Any]]:
        run = cmp.primary
        if run and run.ok and run.parsed:
            return run.parsed
        return None

    async def _vision_fast(
        self,
        description: str,
        image_data_uri: Optional[str],
        seed_bbox: Optional[BoundingBox] = None,
        *,
        relocate: bool = False,
        continue_track: bool = False,
        click_lock: bool = False,
    ) -> tuple[VisionResult, AgentComparison]:
        """Single-model vision for the live tracking loop (fast)."""
        if not image_data_uri:
            return VisionResult(object_label=description, detected=False, context="Frame unavailable"), AgentComparison(agent="vision")
        system = prompts.vision_track_system()
        user = prompts.vision_track_user(
            description,
            seed_bbox,
            relocate=relocate,
            continue_track=continue_track,
            click_lock=click_lock,
        )
        run = await self._call_primary(system, user, image_data_uri)
        target = infer_target_class(description)
        parsed = _safe(VisionResult, run.parsed if run.ok else None)
        raw = parsed.model_dump() if parsed else {}
        if parsed and parsed.detected and parsed.bounding_box:
            label = sanitize_label(parsed.object_label, description)
            appearance = sanitize_appearance(
                raw.get("appearance") or parsed.object_label,
                label,
            )
            ident = sanitize_identity_hint(raw.get("identity_hint"))
            merged = VisionResult(
                object_label=label,
                object_class=parsed.object_class or target,
                detected=True,
                confidence=round(_norm_conf(parsed), 3),
                bounding_box=clamp_bbox_from_result(parsed),
                context=sanitize_label(parsed.context, "in frame"),
                appearance=appearance,
                identity_hint=ident,
            )
        elif parsed:
            merged = VisionResult(
                object_label=sanitize_label(parsed.object_label, description),
                object_class=parsed.object_class or target,
                detected=False,
                confidence=0.0,
                context=sanitize_label(parsed.context, "not visible"),
                appearance=sanitize_appearance(raw.get("appearance")),
            )
        else:
            merged = VisionResult(
                object_label=description,
                object_class=target,
                detected=False,
                confidence=0.0,
                context="detection failed",
            )
        return merged, AgentComparison(agent="vision", primary=run, secondary=None)

    async def _run_fast(
        self,
        req: WatchRequest,
        camera: Camera,
        all_cameras: list[Camera],
        nyc: NYCDataService,
    ) -> WatchResponse:
        log: list[str] = []
        image = req.image_data_uri or await nyc.snapshot_data_uri(camera)
        seed = req.bounding_box
        desc = req.object_description.strip() or "vehicle or person"
        is_click = "click" in desc.lower()
        continue_track = bool(seed and not is_click)

        vision, vcmp = await self._vision_fast(
            desc,
            image,
            seed_bbox=seed,
            continue_track=continue_track,
            click_lock=is_click,
        )
        comparisons = [vcmp]

        # Lock stability: on a continue frame, reject a detection that jumped
        # far from the previous box unless it is high-confidence. This keeps the
        # lock on the SAME object when a person and a vehicle share the frame.
        if (
            continue_track
            and vision.detected
            and vision.bounding_box
            and seed is not None
        ):
            drift = bbox_center_drift(seed.model_dump(), vision.bounding_box.model_dump())
            if drift > _MAX_TRACK_DRIFT and vision.confidence < 0.6:
                log.append("Ignored a look-alike in frame")
                vision = VisionResult(
                    object_label=vision.object_label,
                    object_class=vision.object_class,
                    detected=False,
                    confidence=0.0,
                    context="locked object left frame",
                    appearance=vision.appearance,
                    identity_hint=vision.identity_hint,
                )

        if is_click:
            if vision.detected:
                log.append(f"Locked onto {vision.object_label}")
                if vision.identity_hint:
                    log.append(f"ID: {vision.identity_hint}")
            else:
                log.append("Lock failed — click directly on the object")

        # Prediction: prefer OBSERVED motion (previous box → current box) over a
        # static frame-edge guess. The previous box is the incoming seed.
        if (
            continue_track
            and vision.detected
            and vision.bounding_box
            and seed is not None
        ):
            paths = motion_paths(seed.model_dump(), vision.bounding_box.model_dump())
        elif vision.detected and vision.bounding_box:
            paths = synthetic_paths_from_bbox(vision.bounding_box.model_dump())
        else:
            paths = [PathPrediction(direction="stop", probability=1.0)]

        heading = (
            paths[0].direction
            if paths and paths[0].direction != "stop"
            else None
        )

        # Re-ID signature: prefer the freshest appearance, else the incoming one.
        relocate_desc = track_description(
            vision.object_label or desc,
            vision.identity_hint,
            vision.appearance,
        )
        if not vision.detected and not vision.appearance and desc:
            relocate_desc = desc

        sightings: list = []
        active_cam = camera
        active_vision = vision
        status = "tracking" if vision.detected else "searching"
        searching_count = 0

        # Quota-aware fan-out: the hackathon cap is 100 RPM / 100K TPM, so we
        # only scan other feeds when the object is about to leave the frame or
        # is already gone — which is also exactly when a handoff matters. While
        # the object sits centered in view we spend just one Primary call/tick.
        near_edge = bool(
            vision.detected
            and vision.bounding_box
            and bbox_near_edge(vision.bounding_box.model_dump())
        )
        # Start looking at the camera ahead once the object is moving into the
        # outer third toward its heading — so we're already waiting for it there.
        leaving = near_edge or (
            vision.detected
            and vision.bounding_box is not None
            and heading_toward_edge(vision.bounding_box.model_dump(), heading)
        )
        should_search = (
            not is_click
            and bool(relocate_desc.strip())
            and (not vision.detected or leaving)
        )
        if should_search:
            provisional = TrackerResult(
                camera_id=camera.id,
                lat=camera.lat,
                lng=camera.lng,
                intersection=Intersection(roads=camera.roads),
            )
            route_pick = pick_handoff_camera(camera, provisional, paths, all_cameras)
            route_cam = route_pick[0] if route_pick else None

            targets = search_targets(camera, all_cameras, paths, route_cam)
            searching_count = len(targets)

            if vision.detected and route_cam and heading:
                log.append(
                    f"Heading {heading} — waiting for it at "
                    f"{route_cam.name.split('@')[0].strip()}"
                )

            sightings = await scan_nearby_cameras(
                self,
                relocate_desc,
                camera,
                all_cameras,
                nyc,
                paths,
                vision,
                route_cam=route_cam,
                force=True,
            )

            # Only hand off once we've actually lost the object on the current
            # feed — otherwise stay put and just note the other sighting. This
            # avoids flapping to a look-alike while we still see the real one.
            still_here = vision.detected and vision.confidence >= 0.5
            best = best_sighting(camera, sightings)
            if (
                best
                and best.camera_id != camera.id
                and best.confidence >= _HANDOFF_COMMIT_CONF
                and not still_here
            ):
                hit = next(c for c in all_cameras if c.id == best.camera_id)
                active_cam = hit
                active_vision = VisionResult(
                    object_label=best.object_label or vision.object_label,
                    object_class=vision.object_class,
                    detected=True,
                    confidence=best.confidence,
                    bounding_box=best.bounding_box,
                    context=f"Matched on {hit.name}",
                    appearance=vision.appearance,
                    identity_hint=vision.identity_hint,
                )
                status = "tracking"
                log.append(
                    f"Found on {hit.name.split('@')[0].strip()} "
                    f"({int(best.confidence * 100)}%) — following"
                )

            if active_cam.id == camera.id and not vision.detected:
                status = "searching"

            # Only let cross-camera geometry override the motion heading once we
            # actually have a second sighting — otherwise keep the observed motion
            # so the map still shows where the object is going.
            hits = [s for s in sightings if s.detected and s.confidence >= 0.48]
            if len(hits) > 1:
                paths = paths_from_sightings(camera, sightings)

        tlat, tlng = tracker_position(
            active_cam, sightings if sightings else []
        )
        if not sightings:
            tlat, tlng = active_cam.lat, active_cam.lng

        tracker = TrackerResult(
            camera_id=active_cam.id,
            lat=tlat,
            lng=tlng,
            intersection=Intersection(roads=active_cam.roads),
        )
        prediction = PredictionResult(paths=paths)

        # Tracking geometry cannot establish incident risk.
        risk: Optional[RiskResult] = None

        handoff: Optional[HandoffInfo] = None
        if active_cam.id != camera.id and active_vision.detected:
            handoff = HandoffInfo(
                camera_id=active_cam.id,
                camera_name=active_cam.name,
                reason=(
                    f"Same {active_vision.object_class} confirmed on "
                    f"{active_cam.name} — following across cameras"
                ),
            )

        # One concise status line per follow-up tick (handoff already logged).
        if not is_click and active_cam.id == camera.id:
            if vision.detected:
                tail = f" · heading {heading}" if heading else ""
                log.append(
                    f"Tracking {vision.object_label} "
                    f"({int(vision.confidence * 100)}%){tail}"
                )
            elif searching_count:
                log.append(f"Lost here — scanning {searching_count} nearby feeds")
            else:
                log.append("Searching for target")

        if is_click:
            status = "tracking" if vision.detected else "lost"

        return WatchResponse(
            camera_id=camera.id,
            active_camera_id=active_cam.id,
            mode=req.mode,
            status=status,
            searching_count=searching_count,
            vision=active_vision,
            tracker=tracker,
            prediction=prediction,
            risk=risk,
            handoff=handoff,
            sightings=sightings,
            comparisons=comparisons,
            log=log,
        )

    @traced
    async def run(
        self,
        req: WatchRequest,
        camera: Camera,
        incidents: list[str],
        all_cameras: list[Camera],
        nyc: NYCDataService,
    ) -> WatchResponse:
        if req.fast:
            return await self._run_fast(req, camera, all_cameras, nyc)

        log: list[str] = []
        comparisons: list[AgentComparison] = []
        compare_models = not req.fast
        scan = not req.fast and not req.skip_camera_scan

        active = camera
        image = req.image_data_uri or await nyc.snapshot_data_uri(active)

        # 1. Vision — dual-model consensus (accuracy critical)
        seed = req.bounding_box
        vision, vcmp = await self._vision_consensus(
            req.object_description, req.mode, image, seed_bbox=seed
        )
        comparisons.append(vcmp)

        if seed:
            log.append("Vision: following user click selection")
        if vision.detected:
            log.append(
                f"Vision: {vision.object_class} \"{vision.object_label}\" "
                f"({int(vision.confidence * 100)}% conf) — {vision.context}"
            )
        else:
            log.append(f"Vision: target not detected — {vision.context}")

        vision_dump = vision.model_dump()

        if not vision.detected:
            return WatchResponse(camera_id=camera.id, active_camera_id=active.id,
                                 mode=req.mode, status="lost", vision=vision,
                                 comparisons=comparisons, log=log)

        # 2–4. Tracker → Prediction → Risk (Primary-only for speed)
        tcmp = await self._run_agent(
            "tracker",
            prompts.TRACKER_SYSTEM,
            prompts.tracker_user(active, vision_dump),
            compare=compare_models,
        )
        comparisons.append(tcmp)
        tracker = _safe(TrackerResult, self._primary(tcmp)) or TrackerResult(
            camera_id=active.id,
            lat=active.lat,
            lng=active.lng,
            intersection=Intersection(roads=active.roads),
        )
        # Anchor map positions in camera metadata rather than generated coordinates.
        tracker.camera_id = active.id
        tracker.lat, tracker.lng = active.lat, active.lng
        log.append(
            f"Tracker: {', '.join(tracker.intersection.roads) or active.name}"
        )

        pcmp = await self._run_agent(
            "prediction",
            prompts.PREDICTION_SYSTEM,
            prompts.prediction_user(tracker.intersection.model_dump(), vision_dump),
            compare=compare_models,
        )
        comparisons.append(pcmp)
        prediction = _safe(PredictionResult, self._primary(pcmp)) or PredictionResult(
            paths=[
                PathPrediction(direction="north", probability=0.4),
                PathPrediction(direction="east", probability=0.35),
                PathPrediction(direction="stop", probability=0.25),
            ]
        )
        log.append(
            "Prediction: "
            + ", ".join(
                f"{p.direction} {int(p.probability * 100)}%"
                for p in prediction.paths
            )
        )

        rcmp = await self._run_agent(
            "risk",
            prompts.RISK_SYSTEM,
            prompts.risk_user(
                [p.model_dump() for p in prediction.paths], incidents
            ),
            compare=compare_models,
        )
        comparisons.append(rcmp)
        risk = _safe(RiskResult, self._primary(rcmp)) or RiskResult(
            path_risks=[
                PathRisk(direction=p.direction, risk_score=0.4, reason="baseline")
                for p in prediction.paths
            ]
        )
        if risk.path_risks:
            worst = max(risk.path_risks, key=lambda r: r.risk_score)
            log.append(
                f"Risk: peak {worst.direction} {int(worst.risk_score * 100)}%"
            )

        # 5. Handoff (vision-confirmed)
        handoff: Optional[HandoffInfo] = None
        if vision.detected:
            edge = vision.bounding_box and bbox_near_edge(
                vision.bounding_box.model_dump()
            )
            pick = pick_handoff_camera(
                active, tracker, prediction.paths, all_cameras
            )
            if pick and (edge or _top_moving(prediction.paths)):
                next_cam, reason = pick
                snap = await nyc.snapshot_data_uri(next_cam)
                confirm, _ = await self._vision_consensus(
                    sanitize_label(vision.object_label, req.object_description),
                    req.mode,
                    snap,
                    seed_bbox=None,
                )
                if confirm.detected and confirm.confidence >= 0.55:
                    handoff = HandoffInfo(
                        camera_id=next_cam.id,
                        camera_name=next_cam.name,
                        reason=reason,
                    )
                    log.append(f"Handoff: {next_cam.name}")

        return WatchResponse(
            camera_id=camera.id,
            active_camera_id=active.id,
            mode=req.mode,
            status="tracking" if vision.detected else "lost",
            vision=vision,
            tracker=tracker,
            prediction=prediction,
            risk=risk,
            handoff=handoff,
            comparisons=comparisons,
            log=log,
        )


def _norm_conf(v: VisionResult) -> float:
    return max(0.0, min(v.confidence, 1.0))


def clamp_bbox_from_result(v: VisionResult) -> Optional[BoundingBox]:
    from ..services.detection import clamp_bbox

    if not v.bounding_box:
        return None
    return clamp_bbox(v.bounding_box.model_dump())


def _top_moving(paths: list[PathPrediction]) -> bool:
    return any(p.direction != "stop" and p.probability > 0.2 for p in paths)


def _safe(model_cls, data: Optional[dict]):
    if not data:
        return None
    try:
        return model_cls.model_validate(data)
    except Exception:  # noqa: BLE001
        return None
