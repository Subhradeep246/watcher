"""Bounded, metadata-only inference journal. No prompts, images, or keys."""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

request_id: ContextVar[str] = ContextVar("request_id", default="")
events: deque[dict] = deque(maxlen=250)


def observed(fn):
    @wraps(fn)
    async def wrapper(self, *args, **kwargs):
        run = await fn(self, *args, **kwargs)
        run.host = self.label
        events.append({
            "id": uuid4().hex,
            "request_id": request_id.get(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": kwargs.get("agent", "vision"),
            "slot": run.provider,
            "host": self.label,
            "model": run.model,
            "ok": run.ok,
            "mocked": run.mocked,
            "latency_ms": run.latency_ms,
            "total_tokens": run.total_tokens,
            "valid_json": bool(run.parsed),
            "status_code": run.status_code,
            "retries": run.retries,
            "error": run.error,
        })
        return run
    return wrapper


def journal() -> dict:
    rows = list(events)
    live = [e for e in rows if not e["mocked"]]
    latencies = sorted(e["latency_ms"] for e in live)
    return {
        "events": rows[::-1],
        "summary": {
            "calls": len(live),
            "successes": sum(e["ok"] for e in live),
            "tokens": sum(e["total_tokens"] or 0 for e in live),
            "p95_ms": latencies[min(len(latencies)-1, int(len(latencies)*0.95))] if latencies else None,
        },
        "retention": "Last 250 calls in this server process; resets on restart.",
    }
