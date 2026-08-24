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
        download_root=Path(settings.whisper_download_root),
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
    transcribed, info = model.transcribe(
        str(audio_path),
        language=settings.whisper_language or None,
        beam_size=5,
        vad_filter=True,
    )
    chunks = list(transcribed)
    logger.info(
        "Transcribed %s: duration=%.2fs language=%s chunks=%d",
        audio_path,
        info.duration if info else 0.0,
        info.language if info else "unknown",
        len(chunks),
    )
    text = " ".join(chunk.text for chunk in chunks)
    if not text.strip():
        logger.warning("Transcription produced empty text for %s", audio_path)
    return text
