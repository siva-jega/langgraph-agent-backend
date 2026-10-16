import os
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.core.nestjs_staging_defaults import resolved_nestjs_internal_secret


class AppSettings(BaseSettings):
    PROJECT_NAME: str = "Workflow Service"
    PROJECT_DESCRIPTION: str = "Workflow automation engine for LangGraph Agent Backend"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Postgres (same DB as user-backend, workflow schema)
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")

    # Redis
    REDIS_URI: str | None = os.getenv("REDIS_URI")
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 20

    # Internal auth. Falls back to the shared staging secret (same value
    # user-backend's InternalSecretGuard defaults to) so local runs without an
    # explicit env still authenticate against user-backend's internal endpoints.
    INTERNAL_SECRET: str | None = resolved_nestjs_internal_secret()

    # NestJS base URL (for SEND_MESSAGE step internal calls)
    NESTJS_URL: str | None = (
        os.getenv("NESTJS_URL")
        or os.getenv("BACKEND_URL")
        or os.getenv("USER_BACKEND_URL")
    )

    # Worker config
    WORKER_CONCURRENCY: int = 5

    # Temporal (for scheduling WaitResumeWorkflow)
    TEMPORAL_ADDRESS: str = os.getenv("TEMPORAL_ADDRESS", "temporal:7233")

    # SQS workflow queue
    WORKFLOW_QUEUE_URL: str | None = os.getenv("WORKFLOW_QUEUE_URL")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    SQS_VISIBILITY_TIMEOUT: int = 300  # seconds; set >= p99 workflow execution time
    SQS_MAX_MESSAGES: int = 10
    SQS_WAIT_SECONDS: int = 20  # long polling

    # Logging
    #: "json" => structured single-line JSON with correlation_id/trace_id/span_id
    #: (required for log-to-trace). "console" => plain text for local dev only.
    #: Defaults to "json" so deployments stay structured even if ENVIRONMENT is unset.
    LOG_FORMAT: str = "json"

    # CORS
    ALLOW_ORIGINS: list[str] = ["*"]
    ALLOW_CREDENTIALS: bool = True
    ALLOW_METHODS: list[str] = ["*"]
    ALLOW_HEADERS: list[str] = ["*"]

    # OpenTelemetry / OTLP
    OTEL_SERVICE_NAME: str = "ai-workflow"
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: str | None = None
    OTEL_DISABLE: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT_DISABLED_VALUES: tuple[str, ...] = (
        "off",
        "false",
        "none",
    )

    # Email (Resend)
    RESEND_API_KEY: str | None = os.getenv("RESEND_API_KEY")
    SENDER_EMAIL: str | None = os.getenv("SENDER_EMAIL")

    # Sentry (disabled for now)
    # SENTRY_DSN: str | None = os.getenv("SENTRY_DSN")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("OTEL_DISABLE", mode="before")
    @classmethod
    def _coerce_otel_off_on_flag(cls, v: Any) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @model_validator(mode="after")
    def _disable_otel_in_dev(self) -> "AppSettings":
        if self.resolved_environment_label() == "development":
            self.OTEL_DISABLE = True
            if not os.getenv("LOG_FORMAT"):
                self.LOG_FORMAT = "console"
        return self

    def resolved_environment_label(self) -> str:
        return (self.ENVIRONMENT or "development").strip().lower()

    def is_development_environment(self) -> bool:
        return self.resolved_environment_label() == "development"

    def log_format_is_console(self) -> bool:
        """True only when ``LOG_FORMAT`` explicitly opts into plain console output."""
        return (self.LOG_FORMAT or "json").strip().lower() in (
            "console",
            "text",
            "plain",
            "pretty",
            "dev",
        )

    def otlp_endpoint_is_disabled(self, raw: str | None) -> bool:
        if raw is None:
            return False
        s = str(raw).strip().lower()
        if not s:
            return True
        disabled = {
            x.strip().lower() for x in self.OTEL_EXPORTER_OTLP_ENDPOINT_DISABLED_VALUES
        }
        return s in disabled

    def otel_resource_service_name(self) -> str:
        return (self.OTEL_SERVICE_NAME or "").strip()


app_settings = AppSettings()
