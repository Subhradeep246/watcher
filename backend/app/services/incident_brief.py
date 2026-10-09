"""Build actionable briefs from existing evidence without another model call."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ..schemas import Camera, IncidentBrief, RiskResult, WatchResponse


def build_brief(result: WatchResponse, camera: Camera, *, source: str,
                image: str | None, incidents: list[str], trace_id: str) -> IncidentBrief:
    vision = result.vision
    detected = bool(vision and vision.detected)
    live_evidence = source in ("nyc_dot", "client_frame") and bool(image)
    handoff = result.handoff if result.active_camera_id != camera.id else None
    risk_cmp = next((c for c in result.comparisons if c.agent == "risk"), None)
    risk_run = risk_cmp.primary if risk_cmp else None
    context_live = bool(incidents) and all("sample" not in i.lower() for i in incidents)
    risk_supported = False
    if risk_run and risk_run.ok and risk_run.parsed and context_live:
        try:
            validated = RiskResult.model_validate(risk_run.parsed)
            risk_supported = bool(validated.path_risks and result.risk == validated)
        except ValueError:
            pass
    worst = max(result.risk.path_risks, key=lambda p: p.risk_score, default=None) if result.risk else None
    priority = "review" if detected and live_evidence and risk_supported and worst and worst.risk_score >= 0.7 else "observe"
    if not live_evidence or not detected or handoff:
        priority = "verify"
    actions = []
    if priority == "verify":
        actions.append("Compare the original and proposed camera frames to verify the target." if handoff
                       else "Verify an available camera frame and select the target before acting.")
    elif priority == "review":
        actions.append(f"Review the {worst.direction} path with an operator; model risk is an estimate.")
    else:
        actions.append("Continue observation; confirm movement on the next frame.")
    if result.handoff:
        actions.append(f"Inspect {result.handoff.camera_name} for the proposed camera handoff.")
    if not context_live:
        actions.append("Connect incident data to replace sample context before assessing real-world risk.")
    elif not risk_supported:
        actions.append("Run a full Scan to assess risk against the available incident context.")
    warnings = []
    description_review = result.description_review
    if description_review and description_review.status == "evaluated":
        actions.append("Compare the requested and reported descriptions with the analyzed frame; TypeSafe probabilities are advisory.")
        warnings.append("TypeSafe checks vision-agent text, not camera pixels or cross-camera identity.")
    elif description_review and description_review.status == "unavailable":
        warnings.append(description_review.note)
    if handoff:
        warnings.append("Cross-camera match is unverified. The frame fingerprint belongs to the original camera.")
    if not live_evidence:
        warnings.append("Sample or unavailable frame: this is not live incident evidence.")
    if not context_live:
        warnings.append("Incident context is sample data; it does not establish a current hazard.")
    if not risk_supported:
        warnings.append("No validated risk-agent output with real incident context for this frame.")
    runs = [r for c in result.comparisons for r in (c.primary, c.secondary) if r]
    if any(not r.ok and not r.mocked for r in runs):
        warnings.append("One or more inference calls failed; inspect CoreWeave logs.")
    if any(r.mocked for r in runs):
        warnings.append("A provider or comparison capability is disabled; inspect CoreWeave logs for details.")
    return IncidentBrief(
        id=trace_id, created_at=datetime.now(timezone.utc).isoformat(),
        title=f"{vision.object_label if detected else 'Unconfirmed target'} · {handoff.camera_name if handoff else camera.name}",
        priority=priority, frame_source=source,
        frame_sha256=hashlib.sha256(image.encode()).hexdigest() if image else None,
        camera_name=camera.name, confidence=vision.confidence if detected else 0,
        summary=(f"Possible match at {handoff.camera_name}; identity requires verification. Original frame: {camera.name}."
                 if handoff else f"{'Detected' if detected else 'Not confirmed'} at {camera.name}. Tracking state: {result.status}."),
        actions=actions, limitations=warnings, incident_context=incidents,
        risk_supported=risk_supported, risk_peak=worst.risk_score if risk_supported and worst else None,
        evidence=[f"{r.host or r.provider}: {r.model} · {'success' if r.ok else 'failed'} · {r.latency_ms} ms" for r in runs],
    )
