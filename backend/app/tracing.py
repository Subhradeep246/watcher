"""W&B Weave tracing — every agent step and model call shows up as a trace.

Active only when WANDB_API_KEY + WANDB_PROJECT are set and `weave` is
installed; otherwise `traced` is a no-op so the app runs unchanged.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, TypeVar

from .config import get_settings

F = TypeVar("F", bound=Callable[..., Any])
log = logging.getLogger("watcher.tracing")

_weave: Any = None


def init_tracing() -> bool:
    """Initialise Weave once at startup. Returns True when tracing is live."""
    global _weave
    s = get_settings()
    if _weave is not None:
        return True
    if not (s.weave_enabled and s.wandb_api_key and s.wandb_project):
        return False
    try:
        import weave
    except ImportError:
        log.warning("weave not installed — `pip install weave` to enable tracing")
        return False
    os.environ.setdefault("WANDB_API_KEY", s.wandb_api_key)
    try:
        weave.init(s.wandb_project)
    except Exception as exc:  # noqa: BLE001 — tracing must never break the app
        log.warning("weave.init failed: %s", exc)
        return False
    _weave = weave
    return True


def tracing_enabled() -> bool:
    return _weave is not None


def _slim_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky / unserialisable args (frames, camera lists, services)."""
    out: dict[str, Any] = {}
    for k, v in inputs.items():
        if k in ("all_cameras", "nyc"):
            continue
        if k == "self":
            label = getattr(v, "label", None)
            if label:
                out["provider"] = f"{label} · {getattr(v, 'model', '')}"
            continue
        if isinstance(v, str) and v.startswith("data:image"):
            out[k] = f"<image {len(v) // 1024} KB>"
            continue
        if k == "req" and getattr(v, "image_data_uri", None):
            v = v.model_copy(update={"image_data_uri": "<image>"})
        out[k] = v
    return out


def traced(fn: F) -> F:
    """Wrap `fn` in a weave.op lazily, so decoration works before init."""
    import functools
    import inspect

    op_cache: dict[str, Any] = {}

    def _op() -> Any:
        if _weave is None:
            return None
        if "op" not in op_cache:
            op_cache["op"] = _weave.op(
                fn, name=fn.__qualname__, postprocess_inputs=_slim_inputs
            )
        return op_cache["op"]

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            op = _op()
            return await (op(*args, **kwargs) if op else fn(*args, **kwargs))

        return awrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        op = _op()
        return op(*args, **kwargs) if op else fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
