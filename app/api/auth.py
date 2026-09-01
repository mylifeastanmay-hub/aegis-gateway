from fastapi import APIRouter, Depends, HTTPException, status
from app.core.security import verify_api_key
from app.schemas.auth import APIKeyCreateRequest, APIKeyInfo, KeyUsageResponse
from app.services.auth import api_key_manager

router = APIRouter(prefix="/api/v1/admin/keys", tags=["Admin & Key Management"])


@router.post("", response_model=APIKeyInfo, status_code=status.HTTP_201_CREATED)
async def create_client_api_key(
    request: APIKeyCreateRequest,
    authenticated_key: str = Depends(verify_api_key)
):
    """
    Admin endpoint to generate a new client API key (`ag_live_...`) with assigned tier and budgets.
    Requires admin API key authentication.
    """
    key_info = api_key_manager.create_key(request)
    return key_info


@router.get("/{key_id}/usage", response_model=KeyUsageResponse, status_code=status.HTTP_200_OK)
async def get_client_key_usage(
    key_id: str,
    authenticated_key: str = Depends(verify_api_key)
):
    """
    Admin endpoint to retrieve usage metrics, current daily spend, and remaining budget for a key.
    Requires admin API key authentication.
    """
    usage = api_key_manager.get_key_usage(key_id)
    if not usage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key identifier '{key_id}' not found."
        )
    return usage
