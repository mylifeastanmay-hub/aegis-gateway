import pytest
from httpx import AsyncClient
from app.schemas.auth import APIKeyCreateRequest, ClientTier
from app.services.auth import api_key_manager


@pytest.mark.asyncio
async def test_api_key_manager_creation_and_tier_assignments():
    # 1. Free Tier Key
    free_req = APIKeyCreateRequest(name="Free Tier Client", tier=ClientTier.FREE)
    free_info = api_key_manager.create_key(free_req)
    assert free_info.api_key.startswith("ag_live_")
    assert free_info.rpm_limit == 60
    assert free_info.daily_budget_dollars == 1.00

    # 2. Enterprise Tier Key
    ent_req = APIKeyCreateRequest(name="Enterprise Tier Client", tier=ClientTier.ENTERPRISE)
    ent_info = api_key_manager.create_key(ent_req)
    assert ent_info.api_key.startswith("ag_live_")
    assert ent_info.rpm_limit == 1200
    assert ent_info.daily_budget_dollars == 1000.00

    # Validate lookups
    assert api_key_manager.validate_key(free_info.api_key) is not None
    assert api_key_manager.validate_key(ent_info.api_key) is not None


@pytest.mark.asyncio
async def test_rejection_of_missing_and_invalid_api_keys(async_client: AsyncClient):
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Auth check"}],
        "stream": False
    }

    # 1. Missing API Key -> 401 Unauthorized
    res_missing = await async_client.post("/v1/chat/completions", json=payload)
    assert res_missing.status_code == 401
    assert "Missing API key" in res_missing.json()["detail"]

    # 2. Invalid API Key -> 403 Forbidden
    res_invalid = await async_client.post(
        "/v1/chat/completions",
        json=payload,
        headers={"Authorization": "Bearer ag_live_invalid_fake_key_9999"}
    )
    assert res_invalid.status_code == 403
    assert "Invalid API key" in res_invalid.json()["detail"]


@pytest.mark.asyncio
async def test_daily_spend_quota_exceeded_response(async_client: AsyncClient):
    # Create key with extremely low custom budget cap of $0.0001
    req = APIKeyCreateRequest(
        name="Low Budget Client",
        tier=ClientTier.FREE,
        custom_daily_budget=0.0001
    )
    key_info = api_key_manager.create_key(req)
    headers = {"Authorization": f"Bearer {key_info.api_key}"}

    # Record spend $0.0002 to breach budget
    await api_key_manager.record_spend(key_info.api_key, cost=0.0002)

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Budget test prompt"}],
        "stream": False
    }

    # Subsequent request fails with 429 budget_exceeded_error
    res = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert res.status_code == 429
    assert "Daily spend budget exceeded" in res.json()["detail"]
