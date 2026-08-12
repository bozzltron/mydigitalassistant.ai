"""Local Whisper transcription via faster-whisper.

Lazy-loads the model on first use and keeps it cached for subsequent calls.
Requires: pip install faster-whisper
Whisper model is downloaded automatically on first transcription.
"""

import logging
from pathlib import Path

from faster_whisper import WhisperModel

from assistant.backend.config import settings

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _load_model() -> WhisperModel:
    """Load (or return cached) Whisper model."""
    global _model
    if _model is not None:
        return _model

    logger.info(
        "Loading Whisper model '%s' on device '%s'...",
        settings.whisper_model,
        settings.whisper_device,
    )
    _model = WhisperModel(
        settings.whisper_model,
        device=settings.whisper_device,
        download_root=Path("/app/.cache/whisper"),
    )
    logger.info("Whisper model loaded.")
    return _model


async def transcribe_audio(audio_path: Path) -> str:
    """Transcribe an audio file and return the text.

    Args:
        audio_path: Path to an audio file (wav, webm, mp3, etc.)

    Returns:
        Transcribed text string.
    """
    model = _load_model()
    transcribed, _ = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=5,
        vad_filter=True,
    )
    return " ".join(chunk.text for chunk in transcribed)
