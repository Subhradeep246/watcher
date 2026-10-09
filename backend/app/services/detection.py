"""Detection helpers — class inference, bbox validation, dual-model consensus."""
from __future__ import annotations

import re
from typing import Literal, Optional

from ..schemas import BoundingBox, VisionResult

ObjectClass = Literal["person", "vehicle", "other"]

_PERSON = re.compile(
    r"\b(person|people|pedestrian|walker|jogger|cyclist|bicyclist|"
    r"man|woman|child|kid|human|crossing)\b",
    re.I,
)
_VEHICLE = re.compile(
    r"\b(car|cars|taxi|cab|truck|bus|van|suv|sedan|vehicle|vehicles|"
    r"motorcycle|motorbike|scooter|delivery|uber|lyft|semi|trailer)\b",
    re.I,
)


def infer_target_class(description: str) -> ObjectClass:
    p = bool(_PERSON.search(description))
    v = bool(_VEHICLE.search(description))
    if p and not v:
        return "person"
    if v and not p:
        return "vehicle"
    if p and v:
        return "other"
    # Click-to-track without a typed description — let vision decide class.
    if re.search(r"\b(click|clicked|selected|pointed)\b", description, re.I):
        return "other"
    return "other"


def sanitize_label(label: str, fallback: str = "tracked object") -> str:
    """Short display label."""
    if not label:
        return fallback
    cleaned = re.sub(r"[^\w\s\-#]", " ", label).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 2 or len(cleaned) > 60:
        return fallback
    return cleaned


def sanitize_appearance(text: str | None, fallback: str = "") -> str | None:
    """Rich visual signature — preserve commas, longer text."""
    if not text or str(text).lower() in ("null", "none", "n/a", ""):
        return fallback or None
    cleaned = re.sub(r"[^\w\s\-#,./'\"]", " ", str(text)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 8:
        return fallback or None
    return cleaned[:220]


def sanitize_identity_hint(hint: str | None) -> str | None:
    if not hint or str(hint).lower() in ("null", "none", "n/a", ""):
        return None
    cleaned = re.sub(r"[^\w\s\-#]", " ", str(hint)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 2 or len(cleaned) > 60:
        return None
    return cleaned


def track_description(
    label: str,
    identity_hint: str | None,
    appearance: str | None = None,
) -> str:
    """Full re-ID string sent to the vision model on nearby cameras and follow-up frames."""
    parts: list[str] = []
    if appearance:
        parts.append(appearance)
    elif label:
        parts.append(label)
    if identity_hint:
        low = identity_hint.lower()
        if not parts or low not in parts[0].lower():
            parts.append(identity_hint)
    if not parts:
        return label or "tracked object"
    return "; ".join(parts)[:240]


def clamp_bbox(box: dict) -> Optional[BoundingBox]:
    try:
        x = max(0, min(int(box["x"]), 980))
        y = max(0, min(int(box["y"]), 980))
        w = max(20, min(int(box["width"]), 1000 - x))
        h = max(20, min(int(box["height"]), 1000 - y))
        return BoundingBox(x=x, y=y, width=w, height=h)
    except (KeyError, TypeError, ValueError):
        return None


def bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    ax2, ay2 = a.x + a.width, a.y + a.height
    bx2, by2 = b.x + b.width, b.y + b.height
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


def _norm_confidence(v: VisionResult) -> float:
    c = getattr(v, "confidence", None)
    if c is None:
        return 0.75 if v.bounding_box else 0.0
    return max(0.0, min(float(c), 1.0))


def _class_ok(v: VisionResult, target: ObjectClass) -> bool:
    if target == "other":
        return True
    oc = getattr(v, "object_class", "other") or "other"
    return oc == target or oc == "other"


def merge_vision(
    primary: Optional[VisionResult],
    secondary: Optional[VisionResult],
    target: ObjectClass,
    fallback_label: str,
    seed_bbox: Optional[BoundingBox] = None,
) -> VisionResult:
    pool: list[VisionResult] = []
    for v in (primary, secondary):
        if not v or not getattr(v, "detected", True):
            continue
        if not v.bounding_box:
            continue
        if not _class_ok(v, target):
            continue
        pool.append(v)

    if not pool:
        return VisionResult(
            object_label=fallback_label,
            object_class=target if target != "other" else "other",
            detected=False,
            confidence=0.0,
            context="Object not detected in frame",
        )

    if seed_bbox:
        pool.sort(
            key=lambda v: (
                bbox_iou(v.bounding_box, seed_bbox) if v.bounding_box else 0,
                _norm_confidence(v),
            ),
            reverse=True,
        )
    else:
        pool.sort(key=_norm_confidence, reverse=True)
    best = pool[0]

    if len(pool) >= 2:
        a, b = pool[0], pool[1]
        if a.bounding_box and b.bounding_box:
            iou = bbox_iou(a.bounding_box, b.bounding_box)
            if iou >= 0.25:
                ba, bb = a.bounding_box, b.bounding_box
                merged = BoundingBox(
                    x=int((ba.x + bb.x) / 2),
                    y=int((ba.y + bb.y) / 2),
                    width=int((ba.width + bb.width) / 2),
                    height=int((ba.height + bb.height) / 2),
                )
                conf = min(0.99, (_norm_confidence(a) + _norm_confidence(b)) / 2 + 0.12)
                return VisionResult(
                    object_label=best.object_label,
                    object_class=best.object_class or target,
                    detected=True,
                    confidence=round(conf, 3),
                    bounding_box=clamp_bbox(merged.model_dump()),
                    context=best.context,
                )

    conf = _norm_confidence(best)
    return VisionResult(
        object_label=best.object_label,
        object_class=best.object_class or target,
        detected=True,
        confidence=round(conf, 3),
        bounding_box=clamp_bbox(best.bounding_box.model_dump())
        if best.bounding_box
        else None,
        context=best.context,
    )
