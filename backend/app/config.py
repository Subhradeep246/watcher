"""Central configuration loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Which host runs the primary vision model: auto | nvidia | coreweave
    # "auto" picks the first one that is configured, in that order.
    primary_provider: str = "auto"
    # Which host runs the comparison model: auto | wandb | none
    secondary_provider: str = "auto"

    # NVIDIA NIM (hosted API catalog, OpenAI-compatible)
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    nvidia_disable_thinking: bool = True
    nvidia_fallback_model: str = "meta/llama-3.2-11b-vision-instruct"

    # CoreWeave — self-hosted NIM / vLLM endpoint (see deploy/coreweave)
    coreweave_base_url: str = ""
    coreweave_api_key: str = ""
    coreweave_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"

    # Weights & Biases — Inference (CoreWeave-backed) + Weave tracing
    wandb_api_key: str = ""
    # "<entity>/<project>" — used for Inference usage tracking and Weave traces.
    wandb_project: str = ""
    wandb_inference_base_url: str = "https://api.inference.wandb.ai/v1"
    wandb_inference_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    wandb_vision_enabled: bool = False
    weave_enabled: bool = True

    # NYC data
    ny511_api_key: str = ""
    socrata_app_token: str = ""

    # Server
    watcher_host: str = "127.0.0.1"
    watcher_port: int = 8000
    watcher_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.watcher_cors_origins.split(",") if o.strip()]

    @property
    def resolved_primary(self) -> str:
        p = self.primary_provider.lower()
        if p != "auto":
            return p
        if self.nvidia_api_key:
            return "nvidia"
        if self.coreweave_base_url:
            return "coreweave"
        return "nvidia"  # disabled, but tells the operator which key to add

    @property
    def resolved_secondary(self) -> str:
        p = self.secondary_provider.lower()
        if p != "auto":
            return p
        if self.wandb_api_key:
            return "wandb"
        return "wandb"


@lru_cache
def get_settings() -> Settings:
    return Settings()
