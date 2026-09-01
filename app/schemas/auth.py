import enum
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class ClientTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., description="Client or application name")
    tier: ClientTier = Field(default=ClientTier.FREE, description="Pricing & limit tier")
    custom_daily_budget: Optional[float] = Field(default=None, ge=0.0, description="Override default daily budget cap in USD")
    custom_rpm: Optional[int] = Field(default=None, ge=1, description="Override default requests per minute limit")


class APIKeyInfo(BaseModel):
    key_id: str = Field(..., description="Public identifier for API key")
    name: str = Field(..., description="Client name")
    tier: ClientTier = Field(..., description="Assigned client tier")
    api_key: Optional[str] = Field(default=None, description="Raw secret key (only present on creation)")
    rpm_limit: int = Field(..., ge=1)
    tpm_limit: int = Field(..., ge=1)
    daily_budget_dollars: float = Field(..., ge=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KeyUsageResponse(BaseModel):
    key_id: str = Field(..., description="Public key identifier")
    name: str = Field(..., description="Client name")
    tier: ClientTier = Field(..., description="Client tier")
    current_daily_spend: float = Field(..., ge=0.0, description="Accumulated daily USD spend")
    daily_budget_dollars: float = Field(..., ge=0.0, description="Maximum allowed daily USD budget")
    remaining_budget_dollars: float = Field(..., ge=0.0, description="Remaining daily USD budget")
    total_requests: int = Field(default=0, ge=0, description="Total requests processed for key")
    is_budget_exceeded: bool = Field(default=False, description="Whether client has breached daily budget cap")
