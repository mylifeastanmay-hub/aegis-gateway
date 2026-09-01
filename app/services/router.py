import logging
from typing import AsyncGenerator, Dict, Optional, Tuple
from fastapi import HTTPException, status
from app.adapters.base import BaseProviderAdapter
from app.schemas.proxy import ChatCompletionRequest, ChatCompletionResponse
from app.services.circuit_breaker import CircuitBreaker

logger = logging.getLogger("aegis.services.router")


class FallbackRouter:
    """
    Intelligent multi-provider router with circuit breaking and zero-downtime failover.
    Attempts primary provider first; seamlessly falls back to secondary provider if primary fails or is OPEN.
    """

    def __init__(
        self,
        primary_adapter: BaseProviderAdapter,
        secondary_adapter: BaseProviderAdapter,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0
    ):
        self.primary_adapter = primary_adapter
        self.secondary_adapter = secondary_adapter

        self._breakers: Dict[str, CircuitBreaker] = {
            primary_adapter.provider_name: CircuitBreaker(
                name=primary_adapter.provider_name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout
            ),
            secondary_adapter.provider_name: CircuitBreaker(
                name=secondary_adapter.provider_name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout
            ),
        }

    def get_circuit_breaker(self, provider_name: str) -> CircuitBreaker:
        if provider_name not in self._breakers:
            self._breakers[provider_name] = CircuitBreaker(name=provider_name)
        return self._breakers[provider_name]

    def get_status(self) -> Dict[str, dict]:
        return {
            name: cb.get_status() for name, cb in self._breakers.items()
        }

    async def generate(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> Tuple[ChatCompletionResponse, str, bool]:
        """
        Executes completion request against primary provider with fallback to secondary provider.
        Returns Tuple of (ChatCompletionResponse, provider_name_used, fallback_triggered).
        """
        primary_name = self.primary_adapter.provider_name
        primary_cb = self.get_circuit_breaker(primary_name)

        # 1. Try Primary Provider if Circuit Breaker allows
        if primary_cb.allow_request():
            try:
                response = await self.primary_adapter.generate(request, headers)
                primary_cb.record_success()
                return response, primary_name, False
            except Exception as exc:
                primary_cb.record_failure()
                logger.warning(
                    f"Primary provider '{primary_name}' failed with error: {exc}. "
                    f"Seamlessly failing over to secondary provider '{self.secondary_adapter.provider_name}'."
                )

        else:
            logger.warning(
                f"Primary provider '{primary_name}' circuit breaker is {primary_cb.state}. "
                f"Bypassing primary and routing directly to secondary '{self.secondary_adapter.provider_name}'."
            )

        # 2. Seamless Failover to Secondary Provider
        secondary_name = self.secondary_adapter.provider_name
        secondary_cb = self.get_circuit_breaker(secondary_name)

        if not secondary_cb.allow_request():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="All upstream LLM providers are currently unavailable (circuits OPEN)."
            )

        try:
            response = await self.secondary_adapter.generate(request, headers)
            secondary_cb.record_success()
            return response, secondary_name, True
        except Exception as exc:
            secondary_cb.record_failure()
            logger.error(f"Secondary provider '{secondary_name}' also failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Both primary ('{primary_name}') and secondary ('{secondary_name}') providers failed."
            )

    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        headers: Optional[Dict[str, str]] = None
    ) -> Tuple[AsyncGenerator[str, None], str, bool]:
        """
        Executes streaming completion request with fallback to secondary provider stream.
        Returns Tuple of (stream_generator, provider_name_used, fallback_triggered).
        """
        primary_name = self.primary_adapter.provider_name
        primary_cb = self.get_circuit_breaker(primary_name)

        if primary_cb.allow_request():
            try:
                stream_gen = self.primary_adapter.generate_stream(request, headers)
                primary_cb.record_success()
                return stream_gen, primary_name, False
            except Exception as exc:
                primary_cb.record_failure()
                logger.warning(f"Primary streaming provider '{primary_name}' failed: {exc}. Failing over to secondary.")

        secondary_name = self.secondary_adapter.provider_name
        secondary_cb = self.get_circuit_breaker(secondary_name)

        if not secondary_cb.allow_request():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="All upstream LLM streaming providers are currently unavailable (circuits OPEN)."
            )

        try:
            stream_gen = self.secondary_adapter.generate_stream(request, headers)
            secondary_cb.record_success()
            return stream_gen, secondary_name, True
        except Exception as exc:
            secondary_cb.record_failure()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Both primary ('{primary_name}') and secondary ('{secondary_name}') streaming providers failed."
            )
