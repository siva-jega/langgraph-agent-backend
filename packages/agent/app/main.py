# Reduce gRPC/abseil stderr noise before any gRPC-using libs load (Milvus, OTLP)
import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")

import logging
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.core.config import app_settings
from app.modules.ops.route import router as ops_router
from app.modules.ops.service import MilvusWarmupService
from app.modules.ops.readiness import ReadinessService
from common.project_settings import bind_project_settings

bind_project_settings(lambda: app_settings)

from app.core.lifecycle import lifespan
from app.modules.routes import router as modules_router

# --- Sentry (disabled for now) ---
# import sentry_sdk
#
# if app_settings.SENTRY_DSN:
#     sentry_sdk.init(
#         dsn=app_settings.SENTRY_DSN,
#         environment=app_settings.ENVIRONMENT,
#         send_default_pii=False,
#     )
# else:
#     logging.getLogger(__name__).info("[Sentry] Disabled – no SENTRY_DSN provided")

from app import otel_utils as _otel_early

# Configure structured JSON logging globally (must happen before any logger is used)
_otel_early.configure_json_logging()

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=app_settings.PROJECT_NAME,
        description=app_settings.PROJECT_DESCRIPTION,
        version=app_settings.VERSION,
        docs_url="/docs" if app_settings.ENVIRONMENT != "production" else None,
        redoc_url=(
            "/redoc" if app_settings.ENVIRONMENT != "production" else None
        ),  # noqa
        lifespan=lifespan,
    )
    app.state.milvus_warmup_service = MilvusWarmupService(app_settings)
    app.state.readiness_service = ReadinessService(app_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.ALLOW_ORIGINS,
        allow_credentials=app_settings.ALLOW_CREDENTIALS,
        allow_methods=app_settings.ALLOW_METHODS,
        allow_headers=app_settings.ALLOW_HEADERS,
    )

    from app import otel_utils
    import sys

    import tools

    otel_utils.instrument_app(app)
    if otel_utils.is_opentelemetry_enabled():
        otel_utils.auto_trace_module_functions(sys.modules["common"], recursive=True)
        otel_utils.auto_trace_module_functions(sys.modules["app"], recursive=True)
        otel_utils.auto_trace_module_functions(sys.modules["tools"], recursive=True)

    @app.get("/health")
    @otel_utils.no_trace
    async def health_check() -> Dict[str, str]:
        """Health check endpoint for the API."""
        return {"status": "healthy", "service": "assistant-api"}

    @app.get("/ready", tags=["Ops"])
    @otel_utils.no_trace
    async def readiness_check():
        """Check startup and configured infrastructure before serving traffic."""
        snapshot = await app.state.readiness_service.snapshot(app)
        return JSONResponse(status_code=snapshot.http_status, content=snapshot.as_dict())

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    # @app.exception_handler(Exception)
    # async def unhandled_exception_handler(request, exc):
    #     logger.error(f"Unhandled exception: {exc}", exc_info=True)
    #     sentry_sdk.capture_exception(exc)
    #     return JSONResponse(
    #         status_code=500,
    #         content={"detail": "Internal server error. Please try again later."},
    #     )

    def custom_openapi():

        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app_settings.PROJECT_NAME,
            version=app_settings.VERSION,
            description=app_settings.PROJECT_DESCRIPTION,
            routes=app.routes,
        )

        openapi_schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
        openapi_schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi

    app.mount("/metrics", make_asgi_app())
    app.include_router(ops_router)
    app.include_router(modules_router, prefix="/api")

    return app


app = create_app()
