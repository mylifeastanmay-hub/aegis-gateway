import pytest
from httpx import AsyncClient
from app.schemas.auth import APIKeyCreateRequest, ClientTier
from app.services.auth import api_key_manager


@pytest.mark.asyncio
async def test_proxy_rate_limit_headers_present(async_client: AsyncClient, test_api_key: str):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Rate limit test message"}],
        "stream": False
    }

    res = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert res.status_code == 200

    assert "x-ratelimit-limit" in res.headers
    assert "x-ratelimit-remaining" in res.headers
    assert "x-ratelimit-reset" in res.headers


@pytest.mark.asyncio
async def test_proxy_rate_limit_throttle_breach_429(async_client: AsyncClient, test_api_key: str):
    # Admin create new key with low RPM limit = 2
    admin_headers = {"Authorization": f"Bearer {test_api_key}"}
    create_req = APIKeyCreateRequest(
        name="Low Limit Client",
        tier=ClientTier.FREE,
        custom_rpm=2
    )
    key_info = api_key_manager.create_key(create_req)
    client_headers = {"Authorization": f"Bearer {key_info.api_key}"}

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hello rate limiter"}],
        "stream": False
    }

    # Request 1 -> 200 OK
    res1 = await async_client.post("/v1/chat/completions", json=payload, headers=client_headers)
    assert res1.status_code == 200

    # Request 2 -> 200 OK
    res2 = await async_client.post("/v1/chat/completions", json=payload, headers=client_headers)
    assert res2.status_code == 200

    # Request 3 -> 429 Too Many Requests
    res3 = await async_client.post("/v1/chat/completions", json=payload, headers=client_headers)
    assert res3.status_code == 429
    assert "retry-after" in res3.headers
    assert "Rate limit exceeded" in res3.json()["detail"]
