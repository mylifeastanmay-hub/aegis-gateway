import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
from app.core.config import settings
from app.schemas.proxy import ChatCompletionRequest

logger = logging.getLogger("aegis.services.cache")


class GatewayCache:
    """
    Sub-5ms response caching service supporting async Redis (via redis.asyncio)
    with a resilient in-memory LRU/TTL dictionary fallback.
    """

    def __init__(self):
        self._enabled = settings.ENABLE_CACHE
        self._default_ttl = settings.CACHE_TTL_SECONDS
        self._redis_url = settings.REDIS_URL
        self._using_redis = False
        self._redis = None
        self._memory_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

    async def initialize(self) -> None:
        """
        Initializes Redis connection if configured, otherwise sets up in-memory fallback.
        """
        if not self._enabled:
            return

        if self._redis_url:
            try:
                import redis.asyncio as aioredis
                client = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0
                )
                await client.ping()
                self._redis = client
                self._using_redis = True
                logger.info(f"GatewayCache connected to Redis at {self._redis_url}")
                return
            except Exception as exc:
                logger.warning(f"Failed to connect to Redis ({exc}). Falling back to in-memory cache.")
                self._using_redis = False
                self._redis = None
        else:
            logger.info("No REDIS_URL configured. GatewayCache operating in in-memory mode.")

    async def close(self) -> None:
        """Closes active Redis connection if open."""
        if self._redis and self._using_redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._using_redis = False

    def generate_cache_key(self, request: ChatCompletionRequest) -> str:
        """
        Generates a deterministic SHA-256 cache key over normalized request parameters.
        """
        canonical_payload = {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": m.content, "name": m.name}
                for m in request.messages
            ],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "n": request.n,
            "stop": request.stop,
            "max_tokens": request.max_tokens,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "user": request.user,
        }

        serialized = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"aegis:cache:{digest}"

    async def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached response dict by key. Returns None if missing or expired.
        Target execution latency: < 5ms.
        """
        if not self._enabled or not cache_key:
            return None

        # 1. Redis mode
        if self._using_redis and self._redis:
            try:
                data = await self._redis.get(cache_key)
                if data:
                    return json.loads(data)
                return None
            except Exception as exc:
                logger.error(f"Redis cache get error ({exc}), falling back to memory.")

        # 2. In-memory fallback mode
        if cache_key in self._memory_cache:
            response_dict, expire_ts = self._memory_cache[cache_key]
            if time.time() < expire_ts:
                return response_dict
            else:
                del self._memory_cache[cache_key]

        return None

    async def set(self, cache_key: str, response: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Stores response dict in cache with configurable TTL.
        """
        if not self._enabled or not cache_key or not response:
            return False

        effective_ttl = ttl if ttl is not None else self._default_ttl

        # 1. Redis mode
        if self._using_redis and self._redis:
            try:
                serialized = json.dumps(response)
                await self._redis.setex(cache_key, effective_ttl, serialized)
                return True
            except Exception as exc:
                logger.error(f"Redis cache set error ({exc}), falling back to memory.")

        # 2. In-memory fallback mode
        expire_ts = time.time() + effective_ttl
        self._memory_cache[cache_key] = (response, expire_ts)
        return True

    async def health_check(self) -> Dict[str, Any]:
        """
        Reports cache operational health and mode.
        """
        if not self._enabled:
            return {"enabled": False, "mode": "disabled"}

        if self._using_redis and self._redis:
            try:
                await self._redis.ping()
                return {
                    "enabled": True,
                    "mode": "redis",
                    "status": "healthy",
                    "redis_url": self._redis_url
                }
            except Exception as exc:
                return {
                    "enabled": True,
                    "mode": "in-memory (redis-degraded)",
                    "status": f"degraded: {str(exc)}",
                    "cached_items": len(self._memory_cache)
                }

        # In-Memory
        active_items = sum(1 for _, exp in self._memory_cache.values() if time.time() < exp)
        return {
            "enabled": True,
            "mode": "in-memory",
            "status": "healthy",
            "cached_items": active_items
        }


gateway_cache = GatewayCache()
