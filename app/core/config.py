import json
from typing import Set, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    UPSTREAM_OPENAI_API_KEY: str = Field(default="", description="Upstream OpenAI API Key")
    UPSTREAM_OPENAI_BASE_URL: str = Field(default="https://api.openai.com/v1", description="Upstream OpenAI Base URL")
    DEFAULT_ROUTING_RULE: str = Field(default="mock", description="Default provider routing rule: 'mock' or 'openai'")
    REDACTION_SENSITIVITY: float = Field(default=0.8, ge=0.0, le=1.0, description="Redaction sensitivity threshold")
    AEGIS_API_KEYS: Union[Set[str], str] = Field(
        default_factory=lambda: {"aegis-dev-key", "aegis-secret-key-1"},
        description="Set of valid client API keys for AegisGateway access"
    )
    ENVIRONMENT: str = Field(default="development", description="Deployment environment")
    PROJECT_NAME: str = Field(default="AegisGateway", description="Service name")

    # Cache Settings
    REDIS_URL: str = Field(default="", description="Redis connection URL (e.g., redis://localhost:6379/0). Empty for in-memory fallback.")
    CACHE_TTL_SECONDS: int = Field(default=3600, ge=1, description="Default response cache TTL in seconds")
    ENABLE_CACHE: bool = Field(default=True, description="Enable response caching layer")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("AEGIS_API_KEYS", mode="before")
    @classmethod
    def parse_api_keys(cls, v: Union[str, Set[str], list]) -> Set[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                try:
                    parsed = json.loads(v)
                    return set(parsed)
                except Exception:
                    pass
            return {k.strip() for k in v.split(",") if k.strip()}
        if isinstance(v, (list, tuple)):
            return set(v)
        return v


settings = Settings()
