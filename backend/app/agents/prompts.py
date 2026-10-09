"""System + user prompt templates for each agent."""
from __future__ import annotations

from ..schemas import BoundingBox, Camera
from ..services.detection import ObjectClass

JSON_RULE = (
    "Respond with ONLY a single valid JSON object. No prose, no markdown, "
    "no code fences. Use double quotes for all keys and string values."
)

VISIBLE_ONLY = (
    " Describe only details directly visible in this frame. Do not infer make, "
    "brand, plate characters, cargo, intent, damage, or absence of features "
    "from blurry or hidden areas. Use null for unreadable identity hints. "
    "A single frame cannot prove movement or identity across cameras. "
    "If the requested color/type/details do not match, return detected=false; "
    "do not substitute a similar object."
)

_CLASS_RULES = {
    "person": (
        "TARGET TYPE: PERSON (pedestrian, cyclist, or human). "
        "Only box a human body or cyclist — never vehicles, signs, or shadows. "
        "Tight box around full body or torso+head."
    ),
    "vehicle": (
        "TARGET TYPE: VEHICLE (car, truck, bus, taxi, van, motorcycle). "
        "Only box the vehicle body — never pedestrians, lane paint, or reflections. "
        "Tight box around the full vehicle silhouette."
    ),
    "other": (
        "Locate exactly what the user described. "
        "Tight box around the object — avoid background clutter."
    ),
}


def vision_track_system() -> str:
    return (
        "You are a forensic traffic-camera analyst. Your job is to lock onto ONE "
        "clicked object and describe every visible detail needed to recognize it "
        "on other cameras minutes later. Return ONLY one JSON object. "
        + JSON_RULE + VISIBLE_ONLY
    )


_DETAIL_RULES = (
    "\nVISIBLE DESCRIPTION RULES:\n"
    "- object_label: SHORT category only (e.g. pedestrian, yellow taxi, white van)\n"
    "- appearance: one short sentence describing visible color, body/clothing shape "
    "and any clearly visible distinctive mark. Omit unknown details and negative claims.\n"
    "- identity_hint: a clearly readable plate or distinct visible marker; otherwise null\n"
    "- context: location in the image. Motion is unknown from a single snapshot.\n"
    "- detected=false: confidence=0, bounding_box=null, appearance=null, identity_hint=null\n"
)


def vision_track_user(
    description: str,
    seed_bbox: BoundingBox | None = None,
    *,
    relocate: bool = False,
    continue_track: bool = False,
    click_lock: bool = False,
) -> str:
    hint = ""
    if click_lock and seed_bbox:
        cx = seed_bbox.x + seed_bbox.width // 2
        cy = seed_bbox.y + seed_bbox.height // 2
        hint = (
            f"\nUSER CLICKED at ({cx},{cy}). Box ONLY the object under the click.\n"
            "Describe only clearly visible colors, shape and distinguishing marks. "
            "If the click does not identify a visible object, set detected=false."
        )
    elif seed_bbox and continue_track:
        hint = (
            f"\nPrevious box: x={seed_bbox.x} y={seed_bbox.y} "
            f"w={seed_bbox.width} h={seed_bbox.height}.\n"
            f"Tracked signature: \"{description}\".\n"
            "Find the SAME individual object — verify every detail in appearance "
            "still matches. Update the box. Refresh appearance if you see more detail."
        )
    elif seed_bbox and not relocate:
        cx = seed_bbox.x + seed_bbox.width // 2
        cy = seed_bbox.y + seed_bbox.height // 2
        hint = (
            f"\nUser clicked near ({cx},{cy}). "
            f"Click box: x={seed_bbox.x} y={seed_bbox.y} "
            f"w={seed_bbox.width} h={seed_bbox.height}.\n"
            "Box the object under that click. Ignore everything else."
        )
    elif relocate:
        hint = (
            f"\nFind this EXACT object on a different camera:\n\"{description}\"\n"
            "Match ALL listed visual details — color, clothing, stickers, plate, "
            "marks. Reject similar-looking objects that miss any detail."
        )
    return (
        f"Target: \"{description}\".{hint}{_DETAIL_RULES}\n"
        '{"detected":bool,"object_class":"person"|"vehicle"|"other",'
        '"object_label":str,"appearance":str,"confidence":number,'
        '"bounding_box":{"x":int,"y":int,"width":int,"height":int},'
        '"identity_hint":str|null,"context":str}\n'
        "bbox coords 0-1000. detected=false if not visible.\n" + JSON_RULE + VISIBLE_ONLY
    )


def vision_system(target_class: ObjectClass) -> str:
    return (
        "You are an expert traffic-camera detector. "
        "Your job is precise object localization in NYC DOT camera snapshots. "
        + _CLASS_RULES[target_class]
        + " If the target is NOT visible, set detected=false and confidence=0. "
        + JSON_RULE + VISIBLE_ONLY
    )


def vision_user(
    description: str,
    mode: str,
    target_class: ObjectClass,
    seed_bbox: BoundingBox | None = None,
) -> str:
    click_hint = ""
    if seed_bbox:
        cx = seed_bbox.x + seed_bbox.width // 2
        cy = seed_bbox.y + seed_bbox.height // 2
        click_hint = (
            f"\nUSER CLICKED on the object at normalized center ({cx}, {cy}).\n"
            f"Click hint box: x={seed_bbox.x}, y={seed_bbox.y}, "
            f"w={seed_bbox.width}, h={seed_bbox.height}.\n"
            "Box ONLY the vehicle or person under the user's click. "
            "Expand tightly around the full object body. "
            "Ignore other objects even if they match the text description."
        )
    return (
        f"Scene: {mode}. Watch target: \"{description}\".\n"
        f"Expected class: {target_class}.{click_hint}\n"
        "Return JSON exactly:\n"
        '{"detected": bool, "object_class": "person"|"vehicle"|"other", '
        '"object_label": str, "confidence": number, '
        '"bounding_box": {"x": int, "y": int, "width": int, "height": int}, '
        '"context": str}\n'
        "Rules:\n"
        "- confidence 0.0-1.0: how certain you are this is the correct target\n"
        "- detected=false if target not in image\n"
        "- bounding_box uses NORMALIZED 0-1000 (x,y top-left; width,height)\n"
        "- object_class must match what you actually boxed\n"
        "- context: position in frame + movement direction if visible"
    )


TRACKER_SYSTEM = (
    "You are the Tracker agent. Map the detected object to the intersection. "
    + JSON_RULE
)


def tracker_user(camera: Camera, vision: dict) -> str:
    return (
        f"Camera id={camera.id}, name=\"{camera.name}\", "
        f"lat={camera.lat}, lng={camera.lng}, roads={camera.roads}.\n"
        f"Detection: {vision}.\n"
        "Return JSON:\n"
        '{"camera_id": str, "lat": number, "lng": number, '
        '"intersection": {"roads": [str], "lanes": [str]}}'
    )


PREDICTION_SYSTEM = (
    "You are the Prediction Planner. Estimate next movement on road network. "
    + JSON_RULE
)


def prediction_user(intersection: dict, vision: dict) -> str:
    oc = vision.get("object_class", "other")
    motion_hint = (
        "Vehicles follow lanes and traffic flow."
        if oc == "vehicle"
        else "Pedestrians use crosswalks and sidewalks."
        if oc == "person"
        else "Estimate plausible movement."
    )
    return (
        f"Intersection: {intersection}.\n"
        f"Detection: {vision}.\n"
        f"{motion_hint}\n"
        'Return JSON: {"paths": [{"direction": str, "probability": number}]}\n'
        "direction: north/south/east/west/stop. 2-4 paths, probabilities ~1.0."
    )


RISK_SYSTEM = (
    "You are the Risk Assessor. Score path risk 0..1 using incidents data. "
    + JSON_RULE
)


def risk_user(paths: list[dict], incidents: list[str]) -> str:
    inc = "; ".join(incidents) if incidents else "no active incidents"
    return (
        f"Paths: {paths}.\nIncidents: {inc}.\n"
        'Return JSON: {"path_risks": [{"direction": str, "risk_score": number, "reason": str}]}'
    )
