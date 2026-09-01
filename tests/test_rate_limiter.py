import asyncio
import pytest
from httpx import AsyncClient
from app.schemas.auth import APIKeyCreateRequest, ClientTier
from app.services.auth import api_key_manager
from app.services.rate_limiter import TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_sliding_window_consumption_and_replenishment():
    limiter = TokenBucketRateLimiter()
    client_key = "test_sliding_window_key"

    # 1. Consume 3 tokens out of 3 limit
    for i in range(3):
        allowed, info = await limiter.is_allowed(client_key, cost=1, limit=3, window=1)
        assert allowed is True
        assert info["remaining"] == (2 - i)

    # 2. 4th request -> Throttle breach with positive retry_after
    allowed_breached, info_breached = await limiter.is_allowed(client_key, cost=1, limit=3, window=1)
    assert allowed_breached is False
    assert info_breached["remaining"] == 0
    assert info_breached["retry_after"] >= 1

    # 3. Wait 1.1s for window refill
    await asyncio.sleep(1.1)

    allowed_after, info_after = await limiter.is_allowed(client_key, cost=1, limit=3, window=1)
    assert allowed_after is True
    assert info_after["remaining"] >= 0


@pytest.mark.asyncio
async def test_rate_limiter_header_generation():
    limiter = TokenBucketRateLimiter()
    info = {
        "limit": 60,
        "remaining": 45,
        "reset": 12,
        "retry_after": 0
    }
    headers = limiter.get_rate_limit_headers(info)
    assert headers["X-RateLimit-Limit"] == "60"
    assert headers["X-RateLimit-Remaining"] == "45"
    assert headers["X-RateLimit-Reset"] == "12"


@pytest.mark.asyncio
async def test_rate_limiter_burst_exhaustion_429_retry_after(async_client: AsyncClient, test_api_key: str):
    # Admin create key with burst limit = 1
    admin_headers = {"Authorization": f"Bearer {test_api_key}"}
    req = APIKeyCreateRequest(
        name="Burst Client",
        tier=ClientTier.FREE,
        custom_rpm=1
    )
    key_info = api_key_manager.create_key(req)
    client_headers = {"Authorization": f"Bearer {key_info.api_key}"}

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Burst test"}],
        "stream": False
    }

    # 1st request -> 200 OK
    res1 = await async_client.post("/v1/chat/completions", json=payload, headers=client_headers)
    assert res1.status_code == 200

    # 2nd request -> 429 Too Many Requests with Retry-After header
    res2 = await async_client.post("/v1/chat/completions", json=payload, headers=client_headers)
    assert res2.status_code == 429
    assert "retry-after" in res2.headers
    assert int(res2.headers["retry-after"]) >= 1
    assert "Rate limit exceeded" in res2.json()["detail"]
