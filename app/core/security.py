from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from app.core.config import settings
from app.services.auth import api_key_manager

bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    api_key_header: str | None = Security(api_key_header_scheme)
) -> str:
    """
    Verifies that the incoming client request provides a valid AegisGateway API key.
    Supports either 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.
    Validates dynamically against APIKeyManager registry and fallback config.
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

    # 1. Check APIKeyManager registry
    key_info = api_key_manager.validate_key(token)
    if key_info:
        return token

    # 2. Check fallback settings keys
    valid_keys = settings.AEGIS_API_KEYS
    if isinstance(valid_keys, str):
        valid_keys = {k.strip() for k in valid_keys.split(",") if k.strip()}

    if token not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key provided.",
        )

    return token
