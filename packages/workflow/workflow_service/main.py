# Reduce gRPC/abseil stderr noise before any gRPC-using libs load (OTLP)
import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from workflow_service.core.config import app_settings
from common.project_settings import bind_project_settings

bind_project_settings(lambda: app_settings)

from workflow_service.core.lifecycle import lifespan
from workflow_service.modules.routes import router as modules_router

from workflow_service import otel_utils as _otel_early

_otel_early.configure_json_logging()

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=app_settings.PROJECT_NAME,
        description=app_settings.PROJECT_DESCRIPTION,
        version=app_settings.VERSION,
        docs_url="/docs" if app_settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if app_settings.ENVIRONMENT != "production" else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.ALLOW_ORIGINS,
        allow_credentials=app_settings.ALLOW_CREDENTIALS,
        allow_methods=app_settings.ALLOW_METHODS,
        allow_headers=app_settings.ALLOW_HEADERS,
    )

    from workflow_service import otel_utils
    import sys

    otel_utils.instrument_app(app)
    if otel_utils.is_opentelemetry_enabled():
        otel_utils.auto_trace_module_functions(sys.modules["common"], recursive=True)
        otel_utils.auto_trace_module_functions(
            sys.modules["workflow_service"], recursive=True
        )

    @app.get("/health")
    @otel_utils.no_trace
    async def health_check():
        return {"status": "healthy", "service": "workflow-service"}

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    app.include_router(modules_router, prefix="/api")

    return app


app = create_app()
