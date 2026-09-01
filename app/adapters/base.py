from abc import ABC, abstractmethod
from typing import AsyncGenerator, Dict, Optional
from app.schemas.proxy import ChatCompletionRequest, ChatCompletionResponse


class BaseProviderAdapter(ABC):
    """
    Abstract base class for all AegisGateway LLM provider adapters.
    Supports both non-streaming and SSE streaming completions.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the unique identifier for this provider adapter."""
        pass

    @abstractmethod
    async def generate(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> ChatCompletionResponse:
        """Generate a complete non-streaming chat completion."""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> AsyncGenerator[str, None]:
        """Generate a Server-Sent Events (SSE) formatted text stream of completion chunks."""
        pass
