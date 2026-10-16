from fastapi import APIRouter, UploadFile, File, HTTPException
from app.modules.voice.service import voice_service
from app.modules.voice.models import TranscriptionResponse

router = APIRouter(prefix="/voice", tags=["Voice"])

@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(audio: UploadFile = File(...)):
    """
    Transcribe audio file to text using OpenAI Whisper.
    """
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file provided")

    try:
        text = await voice_service.transcribe_audio(audio)
        return TranscriptionResponse(text=text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
