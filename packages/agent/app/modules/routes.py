"""Single router for all modules. Add new module routers here."""

import logging
from pathlib import Path
from fastapi import APIRouter, Request

from app.modules.agents.route import router as agents_router
from app.modules.tools import router as tools_router
from app.modules.copilot import router as copilot_router
from app.modules.product_summary import router as product_summary_router
from app.modules.product_faq_generation.route import (
    public_router as product_faq_public_router,
    router as product_faq_generation_router,
)
from app.modules.persona_generation import router as persona_generation_router
from app.modules.ad_draft import router as ad_draft_router
from app.modules.voice import router as voice_router
from app.modules.evals.route import router as evals_router

router = APIRouter()
router.include_router(agents_router)
router.include_router(tools_router)
router.include_router(copilot_router)
router.include_router(product_summary_router)
router.include_router(product_faq_generation_router)
router.include_router(product_faq_public_router)
router.include_router(voice_router)
router.include_router(evals_router)
router.include_router(persona_generation_router)
router.include_router(ad_draft_router)
