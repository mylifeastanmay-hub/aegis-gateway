import json
import pytest
from httpx import AsyncClient
from app.api.telemetry import get_telemetry_stream
from app.schemas.telemetry import TelemetrySummary
from app.services.telemetry import TelemetryService, telemetry_service


@pytest.mark.asyncio
async def test_token_cost_savings_calculation():
    service = TelemetryService()
    await service.reset()

    # Record cache hit: 2000 input tokens, 1000 output tokens
    # Expected cost savings: (2000/1000 * 0.0015) + (1000/1000 * 0.0020) = 0.0030 + 0.0020 = $0.0050
    await service.record_event(
        model="gpt-4o",
        input_tokens=2000,
        output_tokens=1000,
        latency_ms=3.0,
        cache_hit=True
    )

    summary = await service.get_metrics_summary()
    assert summary.cache_hits == 1
    assert summary.estimated_dollars_saved == 0.0050
    await service.reset()


@pytest.mark.asyncio
async def test_entity_level_pii_threat_counters():
    service = TelemetryService()
    await service.reset()

    pii_entities = {
        "credit_cards": 2,
        "api_keys": 3,
        "emails": 5,
        "phones": 1,
        "ips": 4
    }

    await service.record_event(
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        latency_ms=12.0,
        cache_hit=False,
        pii_entities=pii_entities
    )

    summary = await service.get_metrics_summary()
    assert summary.pii_breakdown.credit_cards == 2
    assert summary.pii_breakdown.api_keys == 3
    assert summary.pii_breakdown.emails == 5
    assert summary.pii_breakdown.phones == 1
    assert summary.pii_breakdown.ips == 4
    assert summary.pii_breakdown.total == 15
    await service.reset()


@pytest.mark.asyncio
async def test_rolling_latency_percentiles_synthetic_distribution():
    service = TelemetryService()
    await service.reset()

    # Feed 100 synthetic latency samples: 1.0ms, 2.0ms, ..., 100.0ms
    for i in range(1, 101):
        await service.record_event(
            model="gpt-4o",
            input_tokens=10,
            output_tokens=10,
            latency_ms=float(i),
            cache_hit=False
        )

    summary = await service.get_metrics_summary()
    assert summary.latency_percentiles_ms.p50 == pytest.approx(50.5, abs=1.0)
    assert summary.latency_percentiles_ms.p95 == pytest.approx(95.0, abs=1.0)
    assert summary.latency_percentiles_ms.p99 == pytest.approx(99.0, abs=1.0)
    await service.reset()


@pytest.mark.asyncio
async def test_telemetry_reset_endpoint_behavior(async_client: AsyncClient, test_api_key: str):
    headers = {"Authorization": f"Bearer {test_api_key}"}

    # Record dummy request
    await telemetry_service.record_event(
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        latency_ms=10.0,
        cache_hit=False
    )

    summary_before = await telemetry_service.get_metrics_summary()
    assert summary_before.total_requests >= 1

    # Reset endpoint call
    reset_res = await async_client.post("/api/v1/telemetry/reset", headers=headers)
    assert reset_res.status_code == 200

    summary_after = await telemetry_service.get_metrics_summary()
    assert summary_after.total_requests == 0
    assert summary_after.cache_hits == 0
    assert summary_after.total_tokens == 0


@pytest.mark.asyncio
async def test_telemetry_stats_schema_validation(async_client: AsyncClient):
    res = await async_client.get("/api/v1/telemetry/stats")
    assert res.status_code == 200

    # Validate json schema via TelemetrySummary Pydantic model
    data = res.json()
    validated = TelemetrySummary.model_validate(data)
    assert isinstance(validated.total_requests, int)
    assert isinstance(validated.cache_hit_ratio_percent, float)
    assert isinstance(validated.estimated_dollars_saved, float)


@pytest.mark.asyncio
async def test_telemetry_sse_stream_handshake_and_frame():
    stream_response = await get_telemetry_stream()
    assert stream_response.status_code == 200
    assert stream_response.media_type == "text/event-stream"
    assert stream_response.headers.get("Cache-Control") == "no-cache"

    # Consume initial frame from generator
    gen = stream_response.body_iterator
    first_chunk = await gen.__anext__()
    assert first_chunk.startswith("data: ")

    payload_json = first_chunk.replace("data: ", "").strip()
    parsed_summary = json.loads(payload_json)
    assert "total_requests" in parsed_summary
    assert "cache_hit_ratio_percent" in parsed_summary
