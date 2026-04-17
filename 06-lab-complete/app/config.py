"""12-factor configuration for the Part 6 production app."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv(".env.local")


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "Production AI Agent"))
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))
    instance_id: str = field(
        default_factory=lambda: os.getenv(
            "INSTANCE_ID",
            os.getenv("HOSTNAME", f"instance-{uuid.uuid4().hex[:8]}"),
        )
    )

    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "gemini"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "gemini-3.1-flash-lite-preview")
    )
    gemini_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        )
    )
    gemini_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
    )

    agent_api_key: str = field(
        default_factory=lambda: os.getenv("AGENT_API_KEY", "dev-key-change-me")
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: os.getenv("ALLOWED_ORIGINS", "*").split(",")
    )

    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    )
    monthly_budget_usd: float = field(
        default_factory=lambda: float(os.getenv("MONTHLY_BUDGET_USD", "10.0"))
    )
    conversation_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("CONVERSATION_TTL_SECONDS", "3600"))
    )
    conversation_history_limit: int = field(
        default_factory=lambda: int(os.getenv("CONVERSATION_HISTORY_LIMIT", "20"))
    )
    model_context_messages: int = field(
        default_factory=lambda: int(os.getenv("MODEL_CONTEXT_MESSAGES", "6"))
    )
    prometheus_enabled: bool = field(
        default_factory=lambda: os.getenv("PROMETHEUS_ENABLED", "true").lower() == "true"
    )
    otel_service_name: str = field(
        default_factory=lambda: os.getenv("OTEL_SERVICE_NAME", "day12-part6-agent")
    )
    otel_exporter_otlp_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )
    otel_exporter_console: bool = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_CONSOLE", "false").lower() == "true"
    )

    def __post_init__(self):
        self.llm_provider = self.llm_provider.strip().lower()
        if self.llm_provider != "gemini":
            raise ValueError("LLM_PROVIDER must be gemini")

        if isinstance(self.allowed_origins, str):
            self.allowed_origins = self.allowed_origins.split(",")
        self.allowed_origins = [origin.strip() for origin in self.allowed_origins if origin.strip()]
        if not self.allowed_origins:
            self.allowed_origins = ["*"]

        if self.environment == "production" and self.agent_api_key == "dev-key-change-me":
            raise ValueError("AGENT_API_KEY must be set in production")
        if self.environment == "production" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY must be set when LLM_PROVIDER=gemini in production")


settings = Settings()
