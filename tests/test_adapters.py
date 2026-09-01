import pytest
import httpx
from app.adapters.mock_provider import MockProviderAdapter
from app.adapters.openai_adapter import OpenAIAdapter
from app.schemas.proxy import ChatCompletionRequest, ChatMessage


@pytest.mark.asyncio
async def test_mock_adapter_direct():
    adapter = MockProviderAdapter(stream_delay=0.0)
    assert adapter.provider_name == "mock"

    req = ChatCompletionRequest(
        model="mock-v1",
        messages=[ChatMessage(role="user", content="Direct test")]
    )

    res = await adapter.generate(req)
    assert res.model == "mock-v1"
    assert "Direct test" in res.choices[0].message.content

    stream_chunks = []
    async for chunk in adapter.generate_stream(req):
        stream_chunks.append(chunk)

    assert len(stream_chunks) > 2
    assert stream_chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_openai_adapter_provider_name():
    async with httpx.AsyncClient() as client:
        adapter = OpenAIAdapter(http_client=client)
        assert adapter.provider_name == "openai"
