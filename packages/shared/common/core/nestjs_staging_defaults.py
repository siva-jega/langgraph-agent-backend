"""
NestJS user-backend URL + internal secret resolution.

Set NESTJS_URL and NESTJS_INTERNAL_SECRET in the environment for internal API calls.

Local dev: set NESTJS_URL=http://localhost:3000 (or your port) in .env to override.
"""

from __future__ import annotations

import os

# Configure these values through the environment.
STAGING_NESTJS_BASE_URL = os.getenv("NESTJS_STAGING_URL", "")

# Must match Nest WS_INTERNAL_SECRET / InternalSecretGuard (x-internal-secret).
STAGING_NESTJS_INTERNAL_SECRET = os.getenv("NESTJS_STAGING_INTERNAL_SECRET", "")


def resolved_nestjs_base_url() -> str:
    v = (
        (os.getenv("NESTJS_URL") or "").strip()
        or (os.getenv("BACKEND_URL") or "").strip()
        or (os.getenv("USER_BACKEND_URL") or "").strip()
        or STAGING_NESTJS_BASE_URL
    )
    return v.rstrip("/")


def resolved_nestjs_internal_secret() -> str:
    return (
        (os.getenv("NESTJS_INTERNAL_SECRET") or "").strip()
        or (os.getenv("INTERNAL_SECRET") or "").strip()
        or (os.getenv("WS_INTERNAL_SECRET") or "").strip()
        or STAGING_NESTJS_INTERNAL_SECRET
    )
