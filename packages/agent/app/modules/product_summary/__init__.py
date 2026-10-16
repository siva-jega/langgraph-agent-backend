"""Product Summary module: summary generation."""

import logging
from fastapi import APIRouter

from app.modules.product_summary.route import router

logger = logging.getLogger(__name__)

__all__ = ["router"]
