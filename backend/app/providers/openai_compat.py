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
from ..schemas import ModelRun
from ..tracing import traced
from .base import LLMProvider, extract_json


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
        self.enabled = bool(self.api_key and self.base_url and self.model)

    @traced
    async def run(
        self,
        *,
        system: str,
        user: str,
        image_data_uri: Optional[str] = None,
        reasoning_effort: str = "default",
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

        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        start = time.perf_counter()
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
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break
            latency = int((time.perf_counter() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            return ModelRun(
                provider=self.name,
                model=self.model,
                ok=True,
                latency_ms=latency,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                raw_text=text,
                parsed=extract_json(text),
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            latency = int((time.perf_counter() - start) * 1000)
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f" — {exc.response.text[:300]}"
            return ModelRun(
                provider=self.name,
                model=self.model,
                ok=False,
                latency_ms=latency,
                raw_text="",
                error=f"{type(exc).__name__}: {exc}{detail}",
            )


def build_primary() -> LLMProvider:
    """Primary vision model: NVIDIA NIM, or a CoreWeave self-hosted endpoint."""
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
        )
    return OpenAICompatProvider(
        slot="primary",
        label="CoreWeave GPU",
        base_url=s.coreweave_base_url,
        # vLLM/NIM accept any bearer when auth is off; keep the provider enabled.
        api_key=s.coreweave_api_key or "none",
        model=s.coreweave_model,
        key_hint="COREWEAVE_BASE_URL",
    )


def build_secondary() -> Optional[LLMProvider]:
    """Comparison model: W&B Inference (CoreWeave GPUs), or none."""
    s = get_settings()
    choice = s.resolved_secondary
    if choice == "wandb":
        headers = {"OpenAI-Project": s.wandb_project} if s.wandb_project else {}
        return OpenAICompatProvider(
            slot="secondary",
            label="W&B Inference",
            base_url=s.wandb_inference_base_url,
            api_key=s.wandb_api_key,
            model=s.wandb_inference_model,
            extra_headers=headers,
            key_hint="WANDB_API_KEY",
        )
    return None
