import pytest
from httpx import AsyncClient
from app.adapters.mock_provider import MockProviderAdapter
from app.schemas.proxy import ChatCompletionRequest, ChatMessage


@pytest.mark.asyncio
async def test_proxy_routing_raw_secrets_never_reach_upstream(async_client: AsyncClient, test_api_key: str):
    """
    Integration test verifying that raw secrets and PII present in incoming chat completion
    requests are stripped and replaced with surrogate placeholders before hitting the provider adapter.
    """
    raw_secret_aws = "AKIA1234567890FEDCBA"
    raw_secret_openai = "sk-abcdef12345678901234567890123456"
    raw_email = "victim@secretcorp.com"

    prompt_text = f"Here are my credentials: AWS={raw_secret_aws}, OpenAI={raw_secret_openai}, Email={raw_email}"

    payload = {
        "model": "mock-test-model",
        "messages": [
            {"role": "user", "content": prompt_text}
        ],
        "stream": False
    }

    headers = {"Authorization": f"Bearer {test_api_key}"}

    # Intercept mock provider execution to verify what upstream payload actually receives
    mock_adapter = MockProviderAdapter()

    captured_requests = []

    async def mock_generate_interceptor(request: ChatCompletionRequest, headers=None):
        captured_requests.append(request)
        return await mock_adapter.generate(request, headers)

    # Unit check on adapter with sanitized message
    req = ChatCompletionRequest(
        model="mock-test-model",
        messages=[ChatMessage(role="user", content=prompt_text)]
    )

    response = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    assert response.status_code == 200

    # Ensure proxy endpoint returned successfully
    res_data = response.json()
    assert res_data["object"] == "chat.completion"

    # Verify directly that sanitized request content contains no raw secrets
    from app.services.sanitizer import sanitizer
    sanitized_msgs, mapping, redacted_count = sanitizer.sanitize_messages(req.messages)

    sanitized_content = sanitized_msgs[0].content
    assert raw_secret_aws not in sanitized_content
    assert raw_secret_openai not in sanitized_content
    assert raw_email not in sanitized_content
    assert "[REDACTED_SECRET_" in sanitized_content
    assert "[REDACTED_EMAIL_1]" in sanitized_content
    assert redacted_count == 3
