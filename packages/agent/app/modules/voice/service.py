import logging
import httpx
from fastapi import HTTPException, UploadFile
from app.core.config import app_settings

logger = logging.getLogger(__name__)

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


class VoiceService:
    def __init__(self):
        self.api_key = app_settings.SARVAM_API_KEY
        self.client = httpx.AsyncClient(timeout=60.0)

    async def transcribe_audio(self, audio_file: UploadFile) -> str:
        """Transcribe audio using Sarvam Saaras v3."""
        if not self.api_key:
            raise HTTPException(status_code=500, detail="SARVAM_API_KEY not configured")

        try:
            response = await self.client.post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": self.api_key},
                data={"model": "saaras:v3", "mode": "transcribe"},
                files={
                    "file": (
                        audio_file.filename or "audio.webm",
                        audio_file.file,
                        audio_file.content_type or "audio/webm",
                    )
                },
            )
        except httpx.HTTPError as e:
            logger.error("Sarvam request failed: %s", e, exc_info=True)
            raise HTTPException(status_code=502, detail="Transcription service unreachable")

        if response.status_code != 200:
            logger.error("Sarvam returned %s: %s", response.status_code, response.text)
            raise HTTPException(status_code=response.status_code, detail="Transcription failed")

        return response.json().get("transcript", "")


voice_service = VoiceService()
