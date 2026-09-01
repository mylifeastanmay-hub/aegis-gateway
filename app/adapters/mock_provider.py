import asyncio
import time
import uuid
from typing import AsyncGenerator, Dict, Optional
from app.adapters.base import BaseProviderAdapter
from app.schemas.proxy import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    CompletionUsage,
)


class MockProviderAdapter(BaseProviderAdapter):
    """
    Deterministic mock provider adapter for local development, fast unit testing,
    and performance benchmarking without external network calls.
    """

    def __init__(self, stream_delay: float = 0.001):
        self._stream_delay = stream_delay

    @property
    def provider_name(self) -> str:
        return "mock"

    async def generate(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> ChatCompletionResponse:
        completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:8]}"
        created_ts = int(time.time())

        last_prompt = "Hello"
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                last_prompt = msg.content
                break

        response_text = f"[AegisGateway Mock Response] Processed prompt: '{last_prompt}' using model '{request.model}'."

        prompt_tokens = sum(len(str(m.content or "").split()) for m in request.messages)
        completion_tokens = len(response_text.split())

        return ChatCompletionResponse(
            id=completion_id,
            object="chat.completion",
            created=created_ts,
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=response_text),
                    finish_reason="stop"
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            )
        )

    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> AsyncGenerator[str, None]:
        completion_id = f"chatcmpl-mockstream-{uuid.uuid4().hex[:8]}"
        created_ts = int(time.time())

        last_prompt = "Hello"
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                last_prompt = msg.content
                break

        tokens = [
            "[AegisGateway ",
            "Mock ",
            "Stream] ",
            "Processed ",
            f"'{last_prompt}' ",
            f"via model '{request.model}'."
        ]

        # First chunk sending role delta
        first_chunk = ChatCompletionChunk(
            id=completion_id,
            object="chat.completion.chunk",
            created=created_ts,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionChunkDelta(role="assistant", content=""),
                    finish_reason=None
                )
            ]
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        # Subsequent token chunks
        for token in tokens:
            if self._stream_delay > 0:
                await asyncio.sleep(self._stream_delay)

            chunk = ChatCompletionChunk(
                id=completion_id,
                object="chat.completion.chunk",
                created=created_ts,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(content=token),
                        finish_reason=None
                    )
                ]
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        # Final chunk with finish_reason
        final_chunk = ChatCompletionChunk(
            id=completion_id,
            object="chat.completion.chunk",
            created=created_ts,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionChunkDelta(),
                    finish_reason="stop"
                )
            ]
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
