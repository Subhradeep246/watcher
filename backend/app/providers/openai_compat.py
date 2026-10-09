"""OpenAI-compatible Chat Completions provider.

One class serves every OpenAI-compatible host we support:
  * NVIDIA NIM        (integrate.api.nvidia.com, or a self-hosted NIM)
  * W&B Inference     (api.inference.wandb.ai — runs on CoreWeave GPUs)
  * CoreWeave         (your own vLLM / NIM deployment on CoreWeave)
"""
from __future__ import annotations

import asyncio
import time
from typing import Literal, Optional

import httpx

from ..config import get_settings
from ..schemas import ModelRun, PredictionResult, RiskResult, TrackerResult, VisionResult
from ..tracing import traced
from ..telemetry import observed
from .base import LLMProvider, extract_json


_OUTPUT_MODELS = {
    "vision": (VisionResult, {"detected", "object_class", "object_label", "confidence", "bounding_box"}),
    "tracker": (TrackerResult, {"camera_id", "lat", "lng", "intersection"}),
    "prediction": (PredictionResult, {"paths"}),
    "risk": (RiskResult, {"path_risks"}),
}


def _validated_json(text: str, agent: str) -> Optional[dict]:
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return None
    spec = _OUTPUT_MODELS.get(agent)
    if spec:
        model, required = spec
        if not required.issubset(parsed):
            return None
        try:
            model.model_validate(parsed)
            if agent == "vision" and parsed["detected"] and not parsed["bounding_box"]:
                return None
        except ValueError:
            return None
    return parsed


def _constrain_standby(payload: dict, agent: str) -> None:
    # This hosted standby model supports NIM's JSON-schema generation. JSON
    # object mode alone can still produce prose on the catalog endpoint.
    spec = _OUTPUT_MODELS.get(agent)
    if payload["model"] == "meta/llama-3.2-11b-vision-instruct" and spec:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": agent, "schema": spec[0].model_json_schema()},
        }


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        slot: Literal["primary", "secondary"],
        label: str,
        base_url: str,
        api_key: str,
        model: str,
        extra_headers: Optional[dict[str, str]] = None,
        extra_body: Optional[dict] = None,
        key_hint: str = "API key",
        supports_vision: bool = True,
        fallback_model: str = "",
    ) -> None:
        # `slot` is the wire key the UI uses: "primary" = primary, "secondary" = secondary.
        self.name = slot
        self.label = label
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.key_hint = key_hint
        self.supports_vision = supports_vision
        self.fallback_model = fallback_model
        self.enabled = bool(self.api_key and self.base_url and self.model)

    @observed
    @traced
    async def run(
        self,
        *,
        system: str,
        user: str,
        image_data_uri: Optional[str] = None,
        reasoning_effort: str = "default",
        agent: str = "vision",
    ) -> ModelRun:
        if not self.enabled:
            return ModelRun(
                provider=self.name,
                model=self.model,
                ok=False,
                mocked=True,
                raw_text="",
                error=f"{self.key_hint} not set — {self.label} disabled",
            )

        if image_data_uri and not self.supports_vision:
            return ModelRun(provider=self.name, model=self.model, ok=False, mocked=True,
                            error=f"{self.label}: image input disabled for this model; text agents remain live")

        content: list[dict] | str
        if image_data_uri:
            content = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": image_data_uri}},
            ]
        else:
            content = user

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.15 if image_data_uri else 0.4,
            "max_tokens": 1024,
            **self.extra_body,
        }
        if reasoning_effort != "default":
            payload["reasoning_effort"] = reasoning_effort
        _constrain_standby(payload, agent)

        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        start = time.perf_counter()
        attempt = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Retry transient rate-limit / overload responses with backoff.
                for attempt in range(3):
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    if resp.status_code in (429, 503) and attempt < 2:
                        if resp.status_code == 503 and attempt == 1 and self.fallback_model:
                            payload["model"] = self.fallback_model
                            payload.pop("chat_template_kwargs", None)
                            _constrain_standby(payload, agent)
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break
            latency = int((time.perf_counter() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            parsed = _validated_json(text, agent)
            return ModelRun(
                provider=self.name,
                model=payload["model"],
                ok=parsed is not None,
                status_code=resp.status_code,
                retries=attempt,
                latency_ms=latency,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                raw_text=text,
                parsed=parsed,
                error=None if parsed is not None else f"{self.label}: invalid {agent} JSON response",
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            latency = int((time.perf_counter() - start) * 1000)
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            return ModelRun(
                provider=self.name,
                model=payload["model"],
                ok=False,
                status_code=status,
                retries=attempt,
                latency_ms=latency,
                raw_text="",
                error=f"{self.label}: HTTP {status}" if status else f"{self.label}: {type(exc).__name__}",
            )


def build_primary() -> LLMProvider:
    """Primary vision model: NVIDIA NIM or CoreWeave self-hosted."""
    s = get_settings()
    choice = s.resolved_primary
    if choice == "nvidia":
        return OpenAICompatProvider(
            slot="primary",
            label="NVIDIA NIM",
            base_url=s.nvidia_base_url,
            api_key=s.nvidia_api_key,
            model=s.nvidia_model,
            # Reasoning models (Nemotron) take 5-25s per step with thinking on —
            # too slow for a 2s tracking loop; the JSON answers match without it.
            extra_body=(
                {"chat_template_kwargs": {"enable_thinking": False}}
                if s.nvidia_disable_thinking
                else None
            ),
            key_hint="NVIDIA_API_KEY",
            fallback_model=s.nvidia_fallback_model,
        )
    if choice == "coreweave":
        return OpenAICompatProvider(
            slot="primary",
            label="CoreWeave GPU",
            base_url=s.coreweave_base_url,
            # vLLM/NIM accept any bearer when auth is off; keep the provider enabled.
            api_key=s.coreweave_api_key or "none",
            model=s.coreweave_model,
            key_hint="COREWEAVE_BASE_URL",
        )
    raise ValueError(f"Unsupported primary provider: {choice}")


def build_secondary() -> Optional[LLMProvider]:
    """Comparison model: W&B Inference (CoreWeave) or none."""
    s = get_settings()
    choice = s.resolved_secondary
    if choice == "wandb":
        headers = {"OpenAI-Project": s.wandb_project} if s.wandb_project else {}
        return OpenAICompatProvider(
            slot="secondary",
            label="W&B Inference · CoreWeave",
            base_url=s.wandb_inference_base_url,
            api_key=s.wandb_api_key,
            model=s.wandb_inference_model,
            extra_headers=headers,
            key_hint="WANDB_API_KEY",
            supports_vision=s.wandb_vision_enabled,
        )
    return None
