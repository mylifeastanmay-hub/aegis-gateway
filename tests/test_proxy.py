import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_chat_completions_unauthorized(async_client: AsyncClient):
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello Aegis"}]
    }
    response = await async_client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_invalid_key(async_client: AsyncClient):
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello Aegis"}]
    }
    headers = {"Authorization": "Bearer invalid-secret-key"}
    response = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chat_completions_mock_non_stream(async_client: AsyncClient, test_api_key: str):
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello AegisGateway testing"}
        ],
        "stream": False
    }
    headers = {"Authorization": f"Bearer {test_api_key}"}
    response = await async_client.post("/v1/chat/completions", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "gpt-4o-mini"
    assert len(data["choices"]) == 1
    assert "AegisGateway Mock Response" in data["choices"][0]["message"]["content"]
    assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_chat_completions_mock_stream(async_client: AsyncClient, test_api_key: str):
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Stream test"}],
        "stream": True
    }
    headers = {"X-API-Key": test_api_key}
    response = await async_client.post("/v1/chat/completions", json=payload, headers=headers)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    lines = response.text.split("\n")
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert len(data_lines) > 0
    assert data_lines[-1] == "data: [DONE]"
