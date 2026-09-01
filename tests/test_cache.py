import asyncio
import time
import pytest
from httpx import AsyncClient
from app.schemas.proxy import ChatCompletionRequest, ChatMessage
from app.services.cache import GatewayCache, gateway_cache


@pytest.mark.asyncio
async def test_cache_key_generation_deterministic():
    cache = GatewayCache()

    req1 = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hello Aegis Cache")],
        temperature=0.7
    )

    req2 = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hello Aegis Cache")],
        temperature=0.7
    )

    req_different = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hello Aegis Cache")],
        temperature=0.9
    )

    key1 = cache.generate_cache_key(req1)
    key2 = cache.generate_cache_key(req2)
    key_diff = cache.generate_cache_key(req_different)

    assert key1.startswith("aegis:cache:")
    assert key1 == key2
    assert key1 != key_diff


@pytest.mark.asyncio
async def test_gateway_cache_in_memory_get_set_ttl():
    cache = GatewayCache()
    await cache.initialize()

    key = "aegis:cache:testkey123"
    payload = {"id": "chatcmpl-test", "content": "Cached Response Content"}

    # Initially missing
    res = await cache.get(key)
    assert res is None

    # Set item with 1s TTL
    set_success = await cache.set(key, payload, ttl=1)
    assert set_success is True

    # Immediate get -> Hit
    res_hit = await cache.get(key)
    assert res_hit == payload

    # Health check mode verification
    health = await cache.health_check()
    assert health["enabled"] is True
    assert "mode" in health

    # Wait for TTL expiration
    await asyncio.sleep(1.1)
    res_expired = await cache.get(key)
    assert res_expired is None

    await cache.close()


@pytest.mark.asyncio
async def test_cache_sub_5ms_performance():
    cache = GatewayCache()
    await cache.initialize()

    key = "aegis:cache:perfbenchmark"
    payload = {"choices": [{"message": {"content": "Fast cache return"}}]}
    await cache.set(key, payload, ttl=60)

    start_time = time.perf_counter()
    _ = await cache.get(key)
    latency_ms = (time.perf_counter() - start_time) * 1000.0

    # Sub-5ms latency assertion
    assert latency_ms < 5.0

    await cache.close()


@pytest.mark.asyncio
async def test_chat_completions_cache_hit_and_miss_headers(async_client: AsyncClient, test_api_key: str):
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": "Caching integration test prompt"}
        ],
        "temperature": 0.5,
        "stream": False
    }
    headers = {"Authorization": f"Bearer {test_api_key}"}

    # First Request -> Cache MISS
    res1 = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert res1.status_code == 200
    assert res1.headers.get("X-Aegis-Cache") == "MISS"

    # Second Request -> Cache HIT
    res2 = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert res2.status_code == 200
    assert res2.headers.get("X-Aegis-Cache") == "HIT"
    assert res2.json() == res1.json()
