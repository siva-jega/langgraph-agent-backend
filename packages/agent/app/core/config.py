"""Single source of config: all settings from env in AppSettings."""

import json
import os
from typing import Any, List, Tuple

from dotenv import load_dotenv

# override=True so .env is authoritative over stale shell/launchctl-exported vars
load_dotenv(override=True)
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from langgraph.checkpoint.memory import MemorySaver

from common.core.nestjs_staging_defaults import STAGING_NESTJS_BASE_URL


class AppSettings(BaseSettings):
    """All app and infrastructure config from env. Single source of truth."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    PROJECT_NAME: str = "LangGraph Agent Backend"
    PROJECT_DESCRIPTION: str = (
        "FastAPI and LangGraph backend for conversational agents and workflows"
    )
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    #: Alias some runtimes use instead of ENVIRONMENT
    APP_ENV: str | None = None
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4

    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    OPENAI_API_BASE: str | None = os.getenv("OPENAI_API_BASE")
    SARVAM_API_KEY: str | None = os.getenv("SARVAM_API_KEY")
    #: OpenAI service tier: auto, default, flex, scale, or priority (lower latency; requires Priority Processing on the OpenAI project).
    OPENAI_SERVICE_TIER: str | None = os.getenv("OPENAI_SERVICE_TIER", "priority")
    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
    GEMINI_API_BASE: str | None = os.getenv("GEMINI_API_BASE")
    TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")
    COHERE_AZURE_API_KEY: str | None = os.getenv("COHERE_AZURE_API_KEY")
    COHERE_AZURE_BASE_URL: str | None = os.getenv("COHERE_AZURE_BASE_URL")
    RESEND_API_KEY: str | None = os.getenv("RESEND_API_KEY")
    SENDER_EMAIL: str | None = os.getenv("SENDER_EMAIL")
    NESTJS_URL: str | None = (
        os.getenv("NESTJS_URL")
        or os.getenv("BACKEND_URL")
        or os.getenv("USER_BACKEND_URL")
        or STAGING_NESTJS_BASE_URL
    )

    # Cal.com OAuth app (same as user-backend). Sent as x-cal-client-id / x-cal-secret-key so Cal.com allows third-party OAuth tokens on event-types/slots/bookings.
    CALCOM_CLIENT_ID: str | None = ""
    CALCOM_CLIENT_SECRET: str | None = None

    # Google OAuth (Gmail channel send)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    SYSTEM_USER_TOKEN: str | None = None

    # Security
    ENCRYPTION_KEY: str | None = os.getenv("ENCRYPTION_KEY")
    #: Secret header value required to call /agents/bench/stream. Set via env var; empty = endpoint disabled.
    BENCH_SECRET: str = os.getenv("BENCH_SECRET", "")

    # AWS / SQS
    AWS_REGION_NAME: str | None = None
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    CHANNELS_INBOUND_QUEUE_URL: str | None = None
    CHANNELS_SQS_BATCH_SIZE: int = 5
    #: Number of pollers that FETCH from SQS. One is enough for current volume;
    #: processing concurrency is governed separately by CHANNELS_SQS_WORKERS.
    CHANNELS_SQS_POLL_CONCURRENCY: int = 1
    #: Number of workers that PROCESS messages concurrently per pod. Effective
    #: cluster-wide concurrency = this x replicaCount (currently 3 pods).
    CHANNELS_SQS_WORKERS: int = 10

    # Redis
    REDIS_URI: str | None = None
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 20

    # Postgres
    CHECKPOINTER_URL: str | None = None
    POSTGRES_URL: str | None = (
        None  # legacy; used as checkpointer if CHECKPOINTER_URL not set
    )
    DATABASE_URL: str | None = None

    # Milvus
    MILVUS_URI: str | None = None
    MILVUS_TOKEN: str | None = None
    MILVUS_ALIAS: str = "default"
    MILVUS_TIMEOUT: int = 30
    MILVUS_MAX_RETRIES: int = 3
    MILVUS_WARMUP_ENABLED: bool = False
    MILVUS_WARMUP_QUERY: str = "black t-shirt"
    MILVUS_WARMUP_TTL_SECONDS: int = Field(default=300, ge=0)
    MILVUS_WARMUP_SCOPES_JSON: str | None = None

    #: Fallback image shown in product carousel cards when a product has no image URL.
    PRODUCT_FALLBACK_IMAGE_URL: str = ""

    #: When True, Shopify/WooCommerce catalog tools run an extra LLM to filter products (~10s). Set false for lower latency.
    ECOMMERCE_PRODUCT_AI_VALIDATION_ENABLED: bool = True
    #: Default chat model: agent persona fallback (repository + LLM nodes) and Shopify ``validate_products_with_ai``.
    DEFAULT_AGENT_MODEL: str = "gemini/gemini-2.5-flash-lite"
    #: Model for Shopify/WooCommerce product relevance validation in ``validate_products_with_ai``. Falls back to ``DEFAULT_AGENT_MODEL``.
    SHOPIFY_PRODUCT_VALIDATION_MODEL: str = ""
    #: Cache Shopify tokens from user-backend for this many seconds per (user_id, connectorId). 0 disables. Cuts repeated slow get_user_data during get_products.
    SHOPIFY_CREDENTIALS_CACHE_TTL_SECONDS: int = Field(default=45, ge=0, le=600)

    # --- Headroom tool-output compression (Phase 1; feature-flagged, default OFF) ---
    #: When True, insert a compression node after the agent ``tools`` node that shrinks large
    #: ToolMessage payloads (JSON/search/log output) before they re-enter the LLM context.
    #: When False the agent graph is byte-identical to today (node is not added at build time).
    #: Default True for staging testing; set HEADROOM_ENABLED=false to disable.
    HEADROOM_ENABLED: bool = True
    #: Skip compression for ToolMessages below this many (estimated) tokens — small results aren't worth it.
    HEADROOM_MIN_TOKENS_TO_COMPRESS: int = Field(default=100, ge=0)
    #: Preserve error/anomaly text verbatim during compression (recommended True).
    HEADROOM_PRESERVE_ERRORS: bool = True

    # Retrieval (embeddings / rerank)
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
    COHERE_RERANK_MODEL: str = "rerank-v3.5"
    RETRIEVAL_DEFAULT_TOP_K: int = 5
    RETRIEVAL_MAX_RETRIES: int = 3
    RETRIEVAL_TIMEOUT: int = 30

    # LangSmith — controls tracing + eval experiments. Tracing is opt-in:
    # set ``LANGSMITH_TRACING=true`` + ``LANGSMITH_API_KEY`` to send traces to
    # smith.langchain.com. ``LANGSMITH_PROJECT`` segments traces into projects
    # (we use ``web-agent-prod`` for live traffic, ``web-agent-evals`` for
    # ``evaluate(...)`` runs — see EVAL_PLAN.md Phase 6).
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_TRACING: bool = False
    LANGSMITH_PROJECT: str = "web-agent-prod"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    # CORS
    ALLOW_ORIGINS: List[str] = ["*"]
    ALLOW_CREDENTIALS: bool = True
    ALLOW_METHODS: List[str] = ["*"]
    ALLOW_HEADERS: List[str] = ["*"]
    ALLOWED_HOSTS: List[str] = ["*"]

    # Logging
    LOG_LEVEL: str = "INFO"
    #: Log output format. "json" => structured single-line JSON with
    #: correlation_id/trace_id/span_id (required for log-to-trace in Loki/Grafana/
    #: Datadog). "console" => human-readable plain text for local dev only.
    #: Defaults to "json" so deployments stay structured even if ENVIRONMENT is
    #: unset; set LOG_FORMAT=console locally for readable logs.
    LOG_FORMAT: str = "json"

    # OpenTelemetry / OTLP (values come from env / .env via BaseSettings)
    OTEL_SERVICE_NAME: str = "ai-api"
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: str | None = None
    OTEL_DISABLE: bool = False
    #: Trace/logs URLs set to these (case-insensitive) disable OTLP export for that signal.
    OTEL_EXPORTER_OTLP_ENDPOINT_DISABLED_VALUES: Tuple[str, ...] = (
        "off",
        "false",
        "none",
    )

    # Escalation classifier
    ESCALATION_DEFAULT_THRESHOLD: float = 0.7

    # Tag classification (feature-flagged, default OFF)
    TAG_CLASSIFICATION_ENABLED: bool = True
    TAG_CLASSIFICATION_MODEL: str = ""
    TAG_CLASSIFICATION_MAX_MESSAGES: int = Field(default=4, ge=1, le=100)

    # Sentry (disabled for now)
    # SENTRY_DSN: str | None = None

    # Checkpoint
    CHECKPOINT_TYPE: str = "postgres"
    CHECKPOINT_PERSISTENCE: bool = True

    @model_validator(mode="after")
    def _resolve_nestjs_url(self) -> "AppSettings":
        if not self.NESTJS_URL:
            self.NESTJS_URL = (
                self.BACKEND_URL or self.USER_BACKEND_URL or STAGING_NESTJS_BASE_URL
            )
        return self

    @field_validator("OTEL_DISABLE", mode="before")
    @classmethod
    def _coerce_otel_off_on_flag(cls, v: Any) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("MILVUS_WARMUP_ENABLED", mode="before")
    @classmethod
    def _coerce_milvus_warmup_enabled(cls, v: Any) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("MILVUS_WARMUP_SCOPES_JSON", mode="before")
    @classmethod
    def _normalize_milvus_warmup_scopes_json(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        try:
            return json.dumps(v)
        except TypeError:
            return str(v)

    def resolved_environment_label(self) -> str:
        """Normalized ``ENVIRONMENT`` / ``APP_ENV`` label for runtime checks."""
        return (self.ENVIRONMENT or self.APP_ENV or "development").strip().lower()

    def is_development_environment(self) -> bool:
        """True when resolved env label is ``development`` (see ``ENVIRONMENT`` / ``APP_ENV``)."""
        return self.resolved_environment_label() == "development"

    def log_format_is_console(self) -> bool:
        """True only when ``LOG_FORMAT`` explicitly opts into plain console output.

        Anything else (including unset) keeps structured JSON, so a missing env
        var can never silently disable log-to-trace correlation in deployments.
        """
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
        """``service.name`` for OTLP Resource (default ``ai-api``)."""
        name = (self.OTEL_SERVICE_NAME or "").strip()
        return name


app_settings = AppSettings()
memory_saver = MemorySaver()
