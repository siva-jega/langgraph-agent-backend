from pydantic import BaseModel


class TranscriptionResponse(BaseModel):
    """Transcription response model."""

    text: str
