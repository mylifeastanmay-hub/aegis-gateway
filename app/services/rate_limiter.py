import asyncio
import logging
import math
import time
from typing import Any, Dict, Optional, Tuple
from app.core.config import settings

logger = logging.getLogger("aegis.services.rate_limiter")

# Atomic Redis Lua script for Token Bucket Rate Limiting
TOKEN_BUCKET_LUA_SCRIPT = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "last_updated")
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

if not tokens then
    tokens = limit
    last_updated = now
else
    local delta = math.max(0, now - last_updated)
    local fill_rate = limit / window
    tokens = math.min(limit, tokens + delta * fill_rate)
    last_updated = now
end

local allowed = 0
local retry_after = 0

if tokens >= cost then
    allowed = 1
    tokens = tokens - cost
else
    allowed = 0
    local fill_rate = limit / window
    if fill_rate > 0 then
        retry_after = math.ceil((cost - tokens) / fill_rate)
    else
        retry_after = window
    end
end

local fill_rate = limit / window
local reset_seconds = math.ceil((limit - tokens) / fill_rate)
redis.call("HMSET", key, "tokens", tokens, "last_updated", now)
redis.call("EXPIRE", key, math.ceil(window * 2))

return { allowed, math.floor(tokens), reset_seconds, retry_after }
"""


class TokenBucketRateLimiter:
    """
    Distributed Token-Bucket Rate Limiter supporting atomic Redis Lua script execution
    with a resilient in-memory sliding token bucket fallback.
    """

    def __init__(self):
        self._redis_url = settings.REDIS_URL
        self._using_redis = False
        self._redis = None
        self._lua_sha = None
        self._memory_buckets: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initializes Redis connection and registers Lua script if configured.
        """
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
                self._lua_sha = await client.script_load(TOKEN_BUCKET_LUA_SCRIPT)
                self._redis = client
                self._using_redis = True
                logger.info(f"TokenBucketRateLimiter connected to Redis at {self._redis_url}")
                return
            except Exception as exc:
                logger.warning(f"Rate Limiter Redis connection failed ({exc}). Using in-memory fallback.")
                self._using_redis = False
                self._redis = None
        else:
            logger.info("TokenBucketRateLimiter operating in in-memory fallback mode.")

    async def close(self) -> None:
        """Closes active Redis connection if open."""
        if self._redis and self._using_redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._using_redis = False

    async def is_allowed(
        self,
        key: str,
        cost: int = 1,
        limit: int = 60,
        window: int = 60
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Atomically checks if cost can be consumed from client token bucket key.
        Returns Tuple[is_allowed: bool, info_dict: dict].
        """
        redis_key = f"aegis:ratelimit:{key}"
        now_ts = time.time()

        # 1. Redis Mode
        if self._using_redis and self._redis and self._lua_sha:
            try:
                res = await self._redis.evalsha(
                    self._lua_sha,
                    1,
                    redis_key,
                    cost,
                    limit,
                    window,
                    now_ts
                )
                allowed_int, remaining, reset_secs, retry_after = res
                is_allowed = (allowed_int == 1)
                info = {
                    "limit": limit,
                    "remaining": int(remaining),
                    "reset": int(reset_secs),
                    "retry_after": int(retry_after) if not is_allowed else 0,
                    "mode": "redis"
                }
                return is_allowed, info
            except Exception as exc:
                logger.error(f"Redis rate limiter error ({exc}). Falling back to memory.")

        # 2. In-Memory Fallback Mode
        async with self._lock:
            bucket = self._memory_buckets.get(key)
            if not bucket:
                tokens = float(limit)
                last_updated = now_ts
            else:
                tokens = bucket["tokens"]
                last_updated = bucket["last_updated"]
                delta = max(0.0, now_ts - last_updated)
                fill_rate = limit / float(window)
                tokens = min(float(limit), tokens + delta * fill_rate)

            fill_rate = limit / float(window)
            if tokens >= cost:
                is_allowed = True
                tokens -= cost
                retry_after = 0
            else:
                is_allowed = False
                retry_after = math.ceil((cost - tokens) / fill_rate) if fill_rate > 0 else window

            reset_secs = math.ceil((limit - tokens) / fill_rate) if fill_rate > 0 else 0

            self._memory_buckets[key] = {
                "tokens": tokens,
                "last_updated": now_ts
            }

            info = {
                "limit": limit,
                "remaining": int(tokens),
                "reset": max(0, int(reset_secs)),
                "retry_after": max(0, int(retry_after)),
                "mode": "in-memory"
            }
            return is_allowed, info

    def get_rate_limit_headers(self, info: Dict[str, Any]) -> Dict[str, str]:
        """
        Generates standard rate-limiting response headers.
        """
        return {
            "X-RateLimit-Limit": str(info.get("limit", 60)),
            "X-RateLimit-Remaining": str(info.get("remaining", 0)),
            "X-RateLimit-Reset": str(info.get("reset", 0)),
        }


rate_limiter = TokenBucketRateLimiter()
