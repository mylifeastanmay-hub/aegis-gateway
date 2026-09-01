from dataclasses import dataclass
from typing import Any, Dict
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from app.core.config import settings
from app.schemas.auth import APIKeyInfo, ClientTier
from app.services.auth import api_key_manager
from app.services.rate_limiter import rate_limiter

bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass
class ClientContext:
    api_key: str
    key_info: APIKeyInfo
    rate_limit_info: Dict[str, Any]


async def get_authenticated_client_context(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    api_key_header: str | None = Security(api_key_header_scheme)
) -> ClientContext:
    """
    Core AegisGateway Governance dependency:
    1. Authenticates Bearer token / X-API-Key.
    2. Enforces Distributed Token-Bucket Rate Limiting per key.
    3. Enforces Dynamic Daily USD Spend Quotas.
    Returns validated ClientContext with rate limit headers.
    """
    token: str | None = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif api_key_header:
        token = api_key_header

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide Bearer token or X-API-Key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Validate API Key against Manager
    key_info = api_key_manager.validate_key(token)
    if not key_info:
        # Check fallback settings keys
        valid_keys = settings.AEGIS_API_KEYS
        if isinstance(valid_keys, str):
            valid_keys = {k.strip() for k in valid_keys.split(",") if k.strip()}

        if token not in valid_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid API key provided.",
            )

        # Fallback key info for default dev keys
        key_info = APIKeyInfo(
            key_id=f"key_fallback_{token[:8]}",
            name="Fallback Developer Key",
            tier=ClientTier.ENTERPRISE,
            rpm_limit=1200,
            tpm_limit=1_000_000,
            daily_budget_dollars=1000.00
        )

    # 2. Check Token-Bucket Rate Limit
    rate_allowed, rate_info = await rate_limiter.is_allowed(
        key=key_info.key_id,
        cost=1,
        limit=key_info.rpm_limit,
        window=60
    )

    if not rate_allowed:
        headers = rate_limiter.get_rate_limit_headers(rate_info)
        headers["Retry-After"] = str(rate_info.get("retry_after", 1))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. RPM limit: {key_info.rpm_limit}. Retry after {rate_info.get('retry_after', 1)} seconds.",
            headers=headers
        )

    # 3. Check Daily USD Spend Budget Quota
    within_budget, current_spend, max_budget = await api_key_manager.check_and_update_budget(token, cost=0.0)
    if not within_budget:
        headers = rate_limiter.get_rate_limit_headers(rate_info)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily spend budget exceeded (${current_spend:.4f} / ${max_budget:.2f}). Contact admin to upgrade tier.",
            headers=headers
        )

    return ClientContext(
        api_key=token,
        key_info=key_info,
        rate_limit_info=rate_info
    )
