import os
from contextlib import asynccontextmanager
import time
import uuid
import httpx
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from app.adapters.base import BaseProviderAdapter
from app.adapters.mock_provider import MockProviderAdapter
from app.adapters.openai_adapter import OpenAIAdapter
from app.api.auth import router as auth_router
from app.api.telemetry import router as telemetry_router
from app.core.config import settings
from app.core.dependencies import ClientContext, get_authenticated_client_context
from app.schemas.proxy import ChatCompletionRequest, ChatCompletionResponse
from app.services.auth import api_key_manager
from app.services.cache import gateway_cache
from app.services.rate_limiter import rate_limiter
from app.services.router import FallbackRouter
from app.services.sanitizer import sanitizer
from app.services.telemetry import telemetry_service

# Robust Cross-Platform & Docker Path Resolution
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager maintaining global httpx.AsyncClient pool and GatewayCache.
    """
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=100, max_connections=500)
    )
    await gateway_cache.initialize()
    await rate_limiter.initialize()
    yield
    await rate_limiter.close()
    await gateway_cache.close()
    if hasattr(app.state, "http_client") and app.state.http_client:
        await app.state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        description="Intelligent Zero-Trust LLM Gateway & Security Proxy",
        lifespan=lifespan
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Static Files and Web UI Dashboard Route
    if os.path.exists(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/dashboard", tags=["Developer Dashboard"], status_code=status.HTTP_200_OK)
        async def get_developer_dashboard():
            """
            Serves the real-time dark-themed AegisGateway Developer Dashboard Web UI.
            """
            index_path = os.path.join(STATIC_DIR, "index.html")
            return FileResponse(index_path, headers={"Cache-Control": "no-cache"})

        @app.get("/", include_in_schema=False)
        async def root_redirect():
            return RedirectResponse(url="/dashboard")

    # Include API Routers
    app.include_router(telemetry_router)
    app.include_router(auth_router)

    # Request ID and Latency Tracing Middleware
    @app.middleware("http")
    async def request_tracing_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response: Response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-MS"] = f"{duration_ms:.2f}"
        return response

    def get_http_client(request: Request) -> httpx.AsyncClient:
        if hasattr(request.app.state, "http_client") and request.app.state.http_client is not None:
            return request.app.state.http_client
        if not hasattr(request.app.state, "_fallback_http_client") or request.app.state._fallback_http_client is None:
            request.app.state._fallback_http_client = httpx.AsyncClient(timeout=60.0)
        return request.app.state._fallback_http_client

    def get_router(request: Request) -> FallbackRouter:
        client = get_http_client(request)
        primary_rule = settings.DEFAULT_ROUTING_RULE.lower()

        if hasattr(request.app.state, "fallback_router") and request.app.state.fallback_router is not None:
            if request.app.state.fallback_router.primary_adapter.provider_name == primary_rule:
                return request.app.state.fallback_router

        if primary_rule == "openai":
            primary = OpenAIAdapter(http_client=client)
            secondary = MockProviderAdapter()
        else:
            primary = MockProviderAdapter()
            secondary = OpenAIAdapter(http_client=client)

        router = FallbackRouter(
            primary_adapter=primary,
            secondary_adapter=secondary,
            failure_threshold=3,
            recovery_timeout=30.0
        )
        request.app.state.fallback_router = router
        return router

    def extract_pii_counts(surrogate_mapping: dict) -> dict:
        counts = {"credit_cards": 0, "api_keys": 0, "emails": 0, "phones": 0, "ips": 0}
        for surrogate in surrogate_mapping.keys():
            if "CREDIT_CARD" in surrogate:
                counts["credit_cards"] += 1
            elif "SECRET" in surrogate or "KEY" in surrogate:
                counts["api_keys"] += 1
            elif "EMAIL" in surrogate:
                counts["emails"] += 1
            elif "PHONE" in surrogate:
                counts["phones"] += 1
            elif "IPV4" in surrogate or "IP" in surrogate:
                counts["ips"] += 1
        return counts

    @app.get("/health", status_code=status.HTTP_200_OK, tags=["System Health"])
    async def health_check(request: Request):
        cache_status = await gateway_cache.health_check()
        router = get_router(request)
        metrics_summary = await telemetry_service.get_metrics_summary()
        return {
            "status": "healthy",
            "project": settings.PROJECT_NAME,
            "environment": settings.ENVIRONMENT,
            "default_provider": settings.DEFAULT_ROUTING_RULE,
            "active_adapters": ["mock", "openai"],
            "cache": cache_status,
            "circuit_breakers": router.get_status(),
            "telemetry": metrics_summary.model_dump()
        }

    @app.get("/v1/telemetry", status_code=status.HTTP_200_OK, tags=["Telemetry & Analytics"], include_in_schema=False)
    async def legacy_get_telemetry_metrics():
        return await telemetry_service.get_metrics_summary()

    @app.post("/v1/chat/completions", tags=["LLM Proxy"])
    async def chat_completions(
        request_body: ChatCompletionRequest,
        raw_request: Request,
        client_ctx: ClientContext = Depends(get_authenticated_client_context)
    ):
        """
        OpenAI-compatible /v1/chat/completions proxy endpoint with rate limiting, spend budget checks,
        telemetry tracking, circuit breaking, caching, PII sanitization, and token restoration.
        """
        start_time = time.perf_counter()
        router = get_router(raw_request)
        tracing_headers = {"X-Request-ID": raw_request.state.request_id}
        rate_headers = rate_limiter.get_rate_limit_headers(client_ctx.rate_limit_info)

        # 1. Caching Layer Check (Non-streaming requests)
        cache_key = None
        if not request_body.stream and settings.ENABLE_CACHE:
            cache_key = gateway_cache.generate_cache_key(request_body)
            cached_response = await gateway_cache.get(cache_key)
            if cached_response:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                usage = cached_response.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 10)
                completion_tokens = usage.get("completion_tokens", 20)

                await telemetry_service.record_event(
                    model=request_body.model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    cache_hit=True,
                    pii_entities={},
                    fallback_used=False,
                    provider="cache"
                )

                resp_headers = {
                    "X-Aegis-Cache": "HIT",
                    "X-Aegis-Provider": "cache",
                    "X-Aegis-Fallback-Triggered": "false",
                    **rate_headers
                }

                return JSONResponse(content=cached_response, headers=resp_headers)

        # 2. Inbound PII & Secrets Sanitization
        sanitized_messages, surrogate_mapping, redacted_count = sanitizer.sanitize_messages(request_body.messages)
        request_body.messages = sanitized_messages
        raw_request.state.pii_redacted_count = redacted_count
        pii_entity_counts = extract_pii_counts(surrogate_mapping)

        # 3. Handle SSE Streaming Completions
        if request_body.stream:
            raw_generator, provider_used, fallback_triggered = await router.generate_stream(request_body, headers=tracing_headers)

            async def stream_restorer():
                async for chunk in raw_generator:
                    yield sanitizer.restore(chunk, surrogate_mapping)

                latency_ms = (time.perf_counter() - start_time) * 1000.0
                await telemetry_service.record_event(
                    model=request_body.model,
                    input_tokens=20,
                    output_tokens=30,
                    latency_ms=latency_ms,
                    cache_hit=False,
                    pii_entities=pii_entity_counts,
                    fallback_used=fallback_triggered,
                    provider=provider_used
                )

            resp_headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Aegis-Cache": "BYPASS",
                "X-Aegis-Provider": provider_used,
                "X-Aegis-Fallback-Triggered": "true" if fallback_triggered else "false",
                **rate_headers
            }

            return StreamingResponse(stream_restorer(), media_type="text/event-stream", headers=resp_headers)

        # 4. Handle Non-Streaming Completion via FallbackRouter
        response_data, provider_used, fallback_triggered = await router.generate(request_body, headers=tracing_headers)

        # Outbound surrogate token restoration
        if surrogate_mapping:
            for choice in response_data.choices:
                if choice.message and isinstance(choice.message.content, str):
                    choice.message.content = sanitizer.restore(choice.message.content, surrogate_mapping)

        # Asynchronously populate cache
        response_dict = response_data.model_dump()
        if cache_key and settings.ENABLE_CACHE:
            await gateway_cache.set(cache_key, response_dict)

        # Record Telemetry Event & Client Spend
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        prompt_tokens = response_data.usage.prompt_tokens if response_data.usage else 0
        completion_tokens = response_data.usage.completion_tokens if response_data.usage else 0

        estimated_cost = (prompt_tokens / 1000.0 * 0.0015) + (completion_tokens / 1000.0 * 0.0020)
        await api_key_manager.record_spend(client_ctx.api_key, cost=estimated_cost)

        await telemetry_service.record_event(
            model=request_body.model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            latency_ms=latency_ms,
            cache_hit=False,
            pii_entities=pii_entity_counts,
            fallback_used=fallback_triggered,
            provider=provider_used
        )

        resp_headers = {
            "X-Aegis-Cache": "MISS",
            "X-Aegis-Provider": provider_used,
            "X-Aegis-Fallback-Triggered": "true" if fallback_triggered else "false",
            **rate_headers
        }

        return JSONResponse(content=response_dict, headers=resp_headers)

    return app


app = create_app()
