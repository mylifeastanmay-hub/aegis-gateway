from datetime import datetime, timezone
from typing import Dict, Optional
from pydantic import BaseModel, Field


class PIIBreakdown(BaseModel):
    credit_cards: int = Field(default=0, ge=0)
    api_keys: int = Field(default=0, ge=0)
    emails: int = Field(default=0, ge=0)
    phones: int = Field(default=0, ge=0)
    ips: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class LatencyPercentiles(BaseModel):
    p50: float = Field(default=0.0, ge=0.0, description="50th percentile (median) latency in ms")
    p95: float = Field(default=0.0, ge=0.0, description="95th percentile latency in ms")
    p99: float = Field(default=0.0, ge=0.0, description="99th percentile latency in ms")


class TelemetryEvent(BaseModel):
    request_id: str = Field(..., description="Unique request tracing ID")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    client_id: Optional[str] = Field(default=None, description="Authenticated client key identifier")
    model: str = Field(..., description="Requested model name")
    provider: str = Field(..., description="Upstream provider used for completion")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(..., ge=0.0, description="Total execution latency in milliseconds")
    pii_detected: bool = Field(default=False, description="Whether PII was detected in the prompt/completion")
    redacted_count: int = Field(default=0, ge=0, description="Number of redacted sensitive tokens/entities")


class TelemetrySummary(BaseModel):
    total_requests: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    cache_hit_ratio_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    total_prompt_tokens: int = Field(default=0, ge=0)
    total_completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_dollars_saved: float = Field(default=0.0, ge=0.0, description="Calculated financial savings from cached responses")
    pii_breakdown: PIIBreakdown = Field(default_factory=PIIBreakdown)
    fallback_triggered_count: int = Field(default=0, ge=0)
    provider_execution_counts: Dict[str, int] = Field(default_factory=dict)
    latency_percentiles_ms: LatencyPercentiles = Field(default_factory=LatencyPercentiles)
