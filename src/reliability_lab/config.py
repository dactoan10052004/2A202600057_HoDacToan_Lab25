from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    name: str
    provider_type: str = "fake"          # "fake" or "openai"
    model: str = "gpt-4o-mini"           # used only when provider_type="openai"
    fail_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    base_latency_ms: int = Field(default=200, gt=0)
    cost_per_1k_tokens: float = Field(ge=0.0)


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = Field(gt=0)
    reset_timeout_seconds: float = Field(gt=0)
    success_threshold: int = Field(gt=0)


class CacheConfig(BaseModel):
    enabled: bool = True
    backend: str = "memory"  # "memory" or "redis"
    ttl_seconds: int = Field(gt=0)
    similarity_threshold: float = Field(ge=0.0, le=1.0)
    redis_url: str = "redis://localhost:6379/0"


class LoadTestConfig(BaseModel):
    requests: int = Field(gt=0)
    concurrency: int = Field(default=1, gt=0)


class ScenarioConfig(BaseModel):
    name: str
    description: str = ""
    provider_overrides: dict[str, float] = Field(default_factory=dict)
    disable_cache: bool = False


class LabConfig(BaseModel):
    providers: list[ProviderConfig]
    circuit_breaker: CircuitBreakerConfig
    cache: CacheConfig
    load_test: LoadTestConfig
    scenarios: list[ScenarioConfig] = Field(default_factory=list)


def load_config(path: str | Path) -> LabConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    return LabConfig.model_validate(raw)
