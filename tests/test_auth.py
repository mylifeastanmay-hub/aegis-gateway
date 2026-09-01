import pytest
from httpx import AsyncClient
from app.schemas.auth import APIKeyCreateRequest, ClientTier
from app.services.auth import api_key_manager


@pytest.mark.asyncio
async def test_api_key_manager_creation_and_validation():
    req = APIKeyCreateRequest(
        name="Test Corp",
        tier=ClientTier.PRO
    )
    info = api_key_manager.create_key(req)

    assert info.key_id.startswith("key_")
    assert info.api_key.startswith("ag_live_")
    assert info.tier == ClientTier.PRO
    assert info.rpm_limit == 300
    assert info.daily_budget_dollars == 50.00

    validated = api_key_manager.validate_key(info.api_key)
    assert validated is not None
    assert validated.key_id == info.key_id


@pytest.mark.asyncio
async def test_spend_quota_tracker_and_budget_exceeded():
    req = APIKeyCreateRequest(
        name="Small Budget Client",
        tier=ClientTier.FREE,
        custom_daily_budget=0.01
    )
    info = api_key_manager.create_key(req)

    # 1. Check initially within budget
    within, spend, budget = await api_key_manager.check_and_update_budget(info.api_key, cost=0.0)
    assert within is True
    assert spend == 0.0
    assert budget == 0.01

    # 2. Record spend $0.006 (within $0.01 cap)
    await api_key_manager.record_spend(info.api_key, cost=0.006)
    within2, spend2, _ = await api_key_manager.check_and_update_budget(info.api_key, cost=0.0)
    assert within2 is True
    assert spend2 == 0.006

    # 3. Record additional spend $0.005 (total $0.011 > $0.01)
    await api_key_manager.record_spend(info.api_key, cost=0.005)
    within3, spend3, _ = await api_key_manager.check_and_update_budget(info.api_key, cost=0.0)
    assert within3 is False
    assert spend3 == 0.011


@pytest.mark.asyncio
async def test_admin_auth_endpoints(async_client: AsyncClient, test_api_key: str):
    headers = {"Authorization": f"Bearer {test_api_key}"}

    # 1. Create client key via POST /api/v1/admin/keys
    create_payload = {
        "name": "Acme Enterprise",
        "tier": "enterprise",
        "custom_daily_budget": 500.0
    }
    res = await async_client.post("/api/v1/admin/keys", json=create_payload, headers=headers)
    assert res.status_code == 201

    data = res.json()
    assert data["name"] == "Acme Enterprise"
    assert data["tier"] == "enterprise"
    assert data["daily_budget_dollars"] == 500.0
    key_id = data["key_id"]
    new_api_key = data["api_key"]

    # 2. Query usage via GET /api/v1/admin/keys/{key_id}/usage
    usage_res = await async_client.get(f"/api/v1/admin/keys/{key_id}/usage", headers=headers)
    assert usage_res.status_code == 200

    usage_data = usage_res.json()
    assert usage_data["key_id"] == key_id
    assert usage_data["daily_budget_dollars"] == 500.0
    assert usage_data["is_budget_exceeded"] is False

    # 3. Verify new_api_key works on proxy endpoint
    completion_payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Auth test prompt"}],
        "stream": False
    }
    proxy_res = await async_client.post(
        "/v1/chat/completions",
        json=completion_payload,
        headers={"Authorization": f"Bearer {new_api_key}"}
    )
    assert proxy_res.status_code == 200
