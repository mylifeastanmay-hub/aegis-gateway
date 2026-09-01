import time
from typing import AsyncGenerator, Dict, Optional
import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient
from app.adapters.base import BaseProviderAdapter
from app.adapters.mock_provider import MockProviderAdapter
from app.core.config import settings
from app.schemas.proxy import ChatCompletionRequest, ChatCompletionResponse
from app.services.circuit_breaker import CircuitBreaker, CircuitState
from app.services.router import FallbackRouter


class FailingProviderAdapter(BaseProviderAdapter):
    """Failing provider adapter for testing circuit breaker tripping and failover."""
    @property
    def provider_name(self) -> str:
        return "failing_primary"

    async def generate(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> ChatCompletionResponse:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upstream primary provider server error 500"
        )

    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> AsyncGenerator[str, None]:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upstream primary streaming server error 500"
        )


def test_circuit_breaker_state_transitions():
    cb = CircuitBreaker(name="test_provider", failure_threshold=3, recovery_timeout=0.2)

    # 1. Initial CLOSED state
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 2. Record 2 failures -> remains CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED

    # 3. 3rd failure -> trips to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False

    # 4. Wait for recovery timeout -> transitions to HALF_OPEN
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True

    # 5. Success in HALF_OPEN -> closes circuit
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


@pytest.mark.asyncio
async def test_fallback_router_automatic_failover():
    primary_failing = FailingProviderAdapter()
    secondary_mock = MockProviderAdapter()

    router = FallbackRouter(
        primary_adapter=primary_failing,
        secondary_adapter=secondary_mock,
        failure_threshold=2
    )

    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Test failover message"}]
    )

    # First call: Primary fails -> Fallback to Secondary succeeds
    res, provider_used, fallback_triggered = await router.generate(req)

    assert provider_used == "mock"
    assert fallback_triggered is True
    assert "AegisGateway Mock Response" in res.choices[0].message.content

    status_dict = router.get_status()
    assert status_dict["failing_primary"]["failure_count"] == 1


@pytest.mark.asyncio
async def test_proxy_endpoint_fallback_headers(async_client: AsyncClient, test_api_key: str):
    # Set default routing rule to openai (which has no valid key and will fail)
    original_rule = settings.DEFAULT_ROUTING_RULE
    settings.DEFAULT_ROUTING_RULE = "openai"

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Circuit breaker HTTP header test"}],
        "stream": False
    }
    headers = {"Authorization": f"Bearer {test_api_key}"}

    try:
        response = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
        assert response.status_code == 200

        # Assert response contains fallback headers
        assert response.headers.get("X-Aegis-Provider") == "mock"
        assert response.headers.get("X-Aegis-Fallback-Triggered") == "true"
        data = response.json()
        assert "AegisGateway Mock Response" in data["choices"][0]["message"]["content"]
    finally:
        settings.DEFAULT_ROUTING_RULE = original_rule
