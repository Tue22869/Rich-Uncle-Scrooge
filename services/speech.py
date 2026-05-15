"""Speech-to-text service using OpenAI Whisper API."""
import os
import logging
import tempfile
from typing import Optional

from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError, BadRequestError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-1"
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # OpenAI Whisper hard limit: 25 MB
# Telegram OGG/Opus voice is roughly ~8 KB/sec. Used to approximate audio duration
# for cost accounting without invoking ffprobe.
_TELEGRAM_OGG_BYTES_PER_SECOND = 8 * 1024

_client: Optional[AsyncOpenAI] = None


def _approximate_audio_seconds(byte_size: int) -> int:
    """Approximate Telegram voice (OGG/Opus) duration from file size. ±20% accuracy."""
    if byte_size <= 0:
        return 0
    return max(1, byte_size // _TELEGRAM_OGG_BYTES_PER_SECOND)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


async def transcribe_audio(
    file_path: str,
    lang: str = "ru",
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """
    Transcribe an audio file using OpenAI Whisper.

    Args:
        file_path: Path to a local audio file (ogg/mp3/m4a/wav/webm).
        lang: ISO-639-1 language hint (e.g. "ru", "en").
        db, user_id: Optional analytics context. If both set, a `whisper` usage
            event with approximate audio_seconds is recorded.

    Returns:
        Transcribed text, or None on any error.
    """
    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        logger.error(f"Cannot stat audio file {file_path}: {e}")
        return None

    if size == 0:
        logger.error(f"Audio file {file_path} is empty")
        return None

    if size > MAX_FILE_SIZE_BYTES:
        logger.error(f"Audio file {file_path} is {size} bytes, exceeds Whisper limit {MAX_FILE_SIZE_BYTES}")
        return None

    client = _get_client()

    try:
        with open(file_path, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                language=lang,
            )
    except AuthenticationError as e:
        logger.error(f"Whisper auth failed (check OPENAI_API_KEY): {e}")
        return None
    except RateLimitError as e:
        logger.error(f"Whisper rate-limited: {e}")
        return None
    except BadRequestError as e:
        logger.error(f"Whisper bad request (corrupt audio?): {e}")
        return None
    except APIError as e:
        logger.error(f"Whisper API error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during transcription: {e}", exc_info=True)
        return None

    text = (transcript.text or "").strip()
    if not text:
        logger.info("Whisper returned empty transcription")
        return None

    # Best-effort cost accounting.
    if db is not None and user_id is not None:
        try:
            from services.analytics import log_event
            from db.models import UsageEventKind
            log_event(
                db,
                user_id=user_id,
                kind=UsageEventKind.WHISPER,
                model=WHISPER_MODEL,
                audio_seconds=_approximate_audio_seconds(size),
                meta={"lang": lang, "chars": len(text)},
            )
        except Exception as e:
            logger.warning(f"whisper usage logging failed: {e}")

    logger.info(f"Transcribed {size} bytes -> {len(text)} chars (lang={lang})")
    return text


async def transcribe_telegram_voice(
    bot,
    file_id: str,
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """
    Download a Telegram voice message and transcribe it.

    Args:
        bot: Telegram Bot instance (from python-telegram-bot).
        file_id: Telegram file_id of the voice/audio message.
        db, user_id: Optional analytics context — forwarded to transcribe_audio.

    Returns:
        Transcribed text, or None on failure.
    """
    temp_path: Optional[str] = None
    try:
        tg_file = await bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as fp:
            temp_path = fp.name
        await tg_file.download_to_drive(temp_path)
        logger.debug(f"Downloaded voice to {temp_path}")
        return await transcribe_audio(temp_path, lang="ru", db=db, user_id=user_id)
    except Exception as e:
        logger.error(f"Error fetching Telegram voice {file_id}: {e}", exc_info=True)
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
