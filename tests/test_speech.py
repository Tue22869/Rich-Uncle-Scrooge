"""Tests for services/speech.py (OpenAI Whisper integration)."""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APIError, AuthenticationError, BadRequestError, RateLimitError

from services import speech


@pytest.fixture
def small_audio_file():
    """Create a tiny temp file that passes the non-empty / under-25MB check."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(b"fake ogg payload")
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _reset_client():
    """Drop any cached AsyncOpenAI client between tests."""
    speech._client = None
    yield
    speech._client = None


def _make_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def _make_api_error(cls):
    """Construct an OpenAI error without going through the real ctor (signatures vary)."""
    err = cls.__new__(cls)
    Exception.__init__(err, "mocked error")
    return err


@pytest.mark.asyncio
async def test_transcribe_success(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(return_value=_make_response("привет мир"))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file, lang="ru")

    assert result == "привет мир"
    fake_client.audio.transcriptions.create.assert_awaited_once()
    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["language"] == "ru"


@pytest.mark.asyncio
async def test_transcribe_strips_whitespace(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(return_value=_make_response("   текст с пробелами  \n"))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result == "текст с пробелами"


@pytest.mark.asyncio
async def test_transcribe_empty_text_returns_none(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(return_value=_make_response("   "))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_auth_error(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(side_effect=_make_api_error(AuthenticationError))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_rate_limit_error(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(side_effect=_make_api_error(RateLimitError))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_bad_request_error(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(side_effect=_make_api_error(BadRequestError))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_generic_api_error(small_audio_file):
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(side_effect=_make_api_error(APIError))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None


@pytest.mark.asyncio
async def test_transcribe_file_missing():
    result = await speech.transcribe_audio("/nonexistent/path/audio.ogg")
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_file_empty():
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        path = f.name
    try:
        result = await speech.transcribe_audio(path)
        assert result is None
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_transcribe_file_too_large(monkeypatch, small_audio_file):
    monkeypatch.setattr(speech, "MAX_FILE_SIZE_BYTES", 1)  # force "too large"
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(return_value=_make_response("text"))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_audio(small_audio_file)

    assert result is None
    fake_client.audio.transcriptions.create.assert_not_called()


@pytest.mark.asyncio
async def test_transcribe_telegram_voice_happy_path(small_audio_file):
    fake_tg_file = MagicMock()

    async def fake_download(path):
        with open(path, "wb") as f:
            f.write(b"some bytes")

    fake_tg_file.download_to_drive = AsyncMock(side_effect=fake_download)

    fake_bot = MagicMock()
    fake_bot.get_file = AsyncMock(return_value=fake_tg_file)

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create = AsyncMock(return_value=_make_response("hi"))

    with patch.object(speech, "_get_client", return_value=fake_client):
        result = await speech.transcribe_telegram_voice(fake_bot, "file_123")

    assert result == "hi"
    fake_bot.get_file.assert_awaited_once_with("file_123")


@pytest.mark.asyncio
async def test_transcribe_telegram_voice_download_error():
    fake_bot = MagicMock()
    fake_bot.get_file = AsyncMock(side_effect=RuntimeError("network down"))

    result = await speech.transcribe_telegram_voice(fake_bot, "file_456")
    assert result is None
