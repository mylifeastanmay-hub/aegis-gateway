import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone, date
from typing import Any, Dict, Optional, Tuple
from app.core.config import settings
from app.schemas.auth import APIKeyCreateRequest, APIKeyInfo, ClientTier, KeyUsageResponse

logger = logging.getLogger("aegis.services.auth")


# Tier Limit Defaults
TIER_LIMITS = {
    ClientTier.FREE: {"rpm": 60, "tpm": 10_000, "budget": 1.00},
    ClientTier.PRO: {"rpm": 300, "tpm": 100_000, "budget": 50.00},
    ClientTier.ENTERPRISE: {"rpm": 1200, "tpm": 1_000_000, "budget": 1000.00},
}


class APIKeyManager:
    """
    High-performance Multi-Tenant API Key Management and Dynamic Spend Quota Service.
    Target validation latency: < 2ms.
    """

    def __init__(self):
        self._lock = asyncio.Lock()

        # In-memory fast lookup maps
        self._keys_by_raw: Dict[str, APIKeyInfo] = {}
        self._keys_by_id: Dict[str, APIKeyInfo] = {}

        # Daily Spend Tracker: { key_id: { "spend": float, "requests": int, "date": date } }
        self._spend_tracker: Dict[str, Dict[str, Any]] = {}

        self._initialize_default_keys()

    def _initialize_default_keys(self) -> None:
        """Initializes system default developer API keys configured in settings."""
        valid_keys = settings.AEGIS_API_KEYS
        if isinstance(valid_keys, str):
            valid_keys = {k.strip() for k in valid_keys.split(",") if k.strip()}

        for raw_key in valid_keys:
            key_id = f"key_dev_{hashlib.sha256(raw_key.encode()).hexdigest()[:8]}"
            info = APIKeyInfo(
                key_id=key_id,
                name="Default System Developer Key",
                tier=ClientTier.ENTERPRISE,
                api_key=raw_key,
                rpm_limit=1200,
                tpm_limit=1_000_000,
                daily_budget_dollars=1000.00
            )
            self._keys_by_raw[raw_key] = info
            self._keys_by_id[key_id] = info
            self._spend_tracker[key_id] = {
                "spend": 0.0,
                "requests": 0,
                "date": datetime.now(timezone.utc).date()
            }

    def create_key(self, request: APIKeyCreateRequest) -> APIKeyInfo:
        """
        Generates a new client API key (`ag_live_<hex>`) with configured tier limits and budgets.
        """
        raw_key = f"ag_live_{uuid.uuid4().hex}"
        key_id = f"key_{uuid.uuid4().hex[:12]}"

        defaults = TIER_LIMITS.get(request.tier, TIER_LIMITS[ClientTier.FREE])
        rpm = request.custom_rpm if request.custom_rpm is not None else defaults["rpm"]
        budget = request.custom_daily_budget if request.custom_daily_budget is not None else defaults["budget"]

        info = APIKeyInfo(
            key_id=key_id,
            name=request.name,
            tier=request.tier,
            api_key=raw_key,
            rpm_limit=rpm,
            tpm_limit=defaults["tpm"],
            daily_budget_dollars=budget
        )

        self._keys_by_raw[raw_key] = info
        self._keys_by_id[key_id] = info
        self._spend_tracker[key_id] = {
            "spend": 0.0,
            "requests": 0,
            "date": datetime.now(timezone.utc).date()
        }

        logger.info(f"Created new API Key '{key_id}' for client '{request.name}' (Tier: {request.tier.value}).")
        return info

    def validate_key(self, api_key: str) -> Optional[APIKeyInfo]:
        """
        Validates an incoming API key string in sub-2ms.
        """
        if not api_key:
            return None
        return self._keys_by_raw.get(api_key)

    async def check_and_update_budget(self, api_key: str, cost: float = 0.0) -> Tuple[bool, float, float]:
        """
        Checks if client API key has sufficient remaining daily USD budget.
        Returns Tuple[is_within_budget: bool, current_spend: float, daily_budget: float].
        """
        info = self.validate_key(api_key)
        if not info:
            return False, 0.0, 0.0

        today = datetime.now(timezone.utc).date()
        async with self._lock:
            tracker = self._spend_tracker.get(info.key_id)
            if not tracker or tracker["date"] != today:
                tracker = {"spend": 0.0, "requests": 0, "date": today}
                self._spend_tracker[info.key_id] = tracker

            current_spend = tracker["spend"]
            budget_cap = info.daily_budget_dollars

            if current_spend + cost > budget_cap:
                logger.warning(f"Client '{info.name}' ({info.key_id}) exceeded daily budget cap (${current_spend:.4f}/${budget_cap:.2f}).")
                return False, current_spend, budget_cap

            return True, current_spend, budget_cap

    async def record_spend(self, api_key: str, cost: float) -> Tuple[float, float]:
        """
        Records USD token spend for client API key after successful completion.
        Returns Tuple[updated_current_spend: float, daily_budget: float].
        """
        info = self.validate_key(api_key)
        if not info:
            return 0.0, 0.0

        today = datetime.now(timezone.utc).date()
        async with self._lock:
            tracker = self._spend_tracker.get(info.key_id)
            if not tracker or tracker["date"] != today:
                tracker = {"spend": 0.0, "requests": 0, "date": today}
                self._spend_tracker[info.key_id] = tracker

            tracker["spend"] += max(0.0, cost)
            tracker["requests"] += 1
            return tracker["spend"], info.daily_budget_dollars

    def get_key_usage(self, key_id: str) -> Optional[KeyUsageResponse]:
        """
        Retrieves usage stats and budget metrics for a given key_id.
        """
        info = self._keys_by_id.get(key_id)
        if not info:
            return None

        today = datetime.now(timezone.utc).date()
        tracker = self._spend_tracker.get(key_id, {"spend": 0.0, "requests": 0, "date": today})
        if tracker["date"] != today:
            tracker = {"spend": 0.0, "requests": 0, "date": today}

        spend = tracker["spend"]
        budget = info.daily_budget_dollars
        remaining = max(0.0, budget - spend)

        return KeyUsageResponse(
            key_id=info.key_id,
            name=info.name,
            tier=info.tier,
            current_daily_spend=round(spend, 6),
            daily_budget_dollars=budget,
            remaining_budget_dollars=round(remaining, 6),
            total_requests=tracker["requests"],
            is_budget_exceeded=(spend >= budget)
        )


api_key_manager = APIKeyManager()
