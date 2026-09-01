import json
import logging
from typing import AsyncGenerator, Dict, Optional
import httpx
from fastapi import HTTPException, status
from app.adapters.base import BaseProviderAdapter
from app.core.config import settings
from app.schemas.proxy import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger("aegis.adapters.openai")


class OpenAIAdapter(BaseProviderAdapter):
    def __init__(self, http_client: httpx.AsyncClient):
        self._http_client = http_client
        self._base_url = settings.UPSTREAM_OPENAI_BASE_URL.rstrip("/")
        self._api_key = settings.UPSTREAM_OPENAI_API_KEY

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if custom_headers:
            # Forward tracing/correlation headers if provided, avoiding auth override
            for k, v in custom_headers.items():
                if k.lower() not in ("authorization", "host"):
                    headers[k] = v
        return headers

    async def generate(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> ChatCompletionResponse:
        url = f"{self._base_url}/chat/completions"
        payload = request.model_dump(exclude_none=True)
        payload["stream"] = False

        try:
            response = await self._http_client.post(
                url,
                json=payload,
                headers=self._get_headers(headers),
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            return ChatCompletionResponse.model_validate(data)
        except httpx.HTTPStatusError as exc:
            logger.error(f"OpenAI upstream error status {exc.response.status_code}: {exc.response.text}")
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Upstream provider error: {exc.response.text}"
            )
        except httpx.RequestError as exc:
            logger.error(f"OpenAI connection failure: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to connect to upstream provider: {str(exc)}"
            )

    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> AsyncGenerator[str, None]:
        url = f"{self._base_url}/chat/completions"
        payload = request.model_dump(exclude_none=True)
        payload["stream"] = True

        try:
            async with self._http_client.stream(
                "POST",
                url,
                json=payload,
                headers=self._get_headers(headers),
                timeout=60.0
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"OpenAI stream error status {response.status_code}: {error_text.decode('utf-8')}")
                    yield f"data: {json.dumps({'error': f'Upstream error {response.status_code}'})}\n\n"
                    return

                async for line in response.aiter_lines():
                    if line:
                        yield f"{line}\n\n"
        except httpx.RequestError as exc:
            logger.error(f"OpenAI stream connection failure: {exc}")
            yield f"data: {json.dumps({'error': f'Connection error: {str(exc)}'})}\n\n"
