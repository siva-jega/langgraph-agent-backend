import logging

from fastapi import APIRouter, HTTPException

from app.modules.persona_generation.models import GeneratePersonaRequest
from app.modules.persona_generation.service import generate_persona

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/persona", tags=["Persona Generation"])


@router.post("/generate")
async def generate_persona_route(payload: GeneratePersonaRequest):
    try:
        return await generate_persona(payload)
    except Exception as e:
        logger.error("persona generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate persona")
