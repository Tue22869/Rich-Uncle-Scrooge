"""Speech-to-text service using SpeechFlow.io API."""
import logging
import asyncio
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# SpeechFlow API credentials
API_KEY_ID = "sKLTeeTIGX9vpp2s"
API_KEY_SECRET = "usDy2871P776xRm6"

# API endpoints
CREATE_URL = "https://api.speechflow.io/asr/file/v1/create"
QUERY_URL = "https://api.speechflow.io/asr/file/v1/query"


async def transcribe_audio(file_path: str, lang: str = "ru") -> Optional[str]:
    """
    Transcribe audio file using SpeechFlow API.
    
    Args:
        file_path: Path to the audio file (local or remote URL)
        lang: Language code (ru, en, etc.)
    
    Returns:
        Transcribed text or None if failed
    """
    headers = {
        "keyId": API_KEY_ID,
        "keySecret": API_KEY_SECRET
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            # Create transcription task
            task_id = await _create_task(session, file_path, lang, headers)
            if not task_id:
                return None
            
            # Query for results
            text = await _query_result(session, task_id, headers)
            return text
            
    except Exception as e:
        logger.error(f"Speech transcription error: {e}")
        return None


async def _create_task(
    session: aiohttp.ClientSession, 
    file_path: str, 
    lang: str, 
    headers: dict
) -> Optional[str]:
    """Create a transcription task."""
    try:
        if file_path.startswith('http'):
            # Remote file
            data = {"lang": lang, "remotePath": file_path}
            async with session.post(CREATE_URL, data=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("code") == 10000:
                        task_id = result.get("taskId")
                        logger.info(f"Created transcription task: {task_id}")
                        return task_id
                    else:
                        logger.error(f"Create task error: {result.get('msg')}")
                else:
                    logger.error(f"Create request failed: {response.status}")
        else:
            # Local file
            url = f"{CREATE_URL}?lang={lang}"
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename='audio.ogg')
                async with session.post(url, data=data, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("code") == 10000:
                            task_id = result.get("taskId")
                            logger.info(f"Created transcription task: {task_id}")
                            return task_id
                        else:
                            logger.error(f"Create task error: {result.get('msg')}")
                    else:
                        logger.error(f"Create request failed: {response.status}")
    except Exception as e:
        logger.error(f"Error creating transcription task: {e}")
    
    return None


async def _query_result(
    session: aiohttp.ClientSession, 
    task_id: str, 
    headers: dict,
    max_attempts: int = 30,
    poll_interval: float = 2.0
) -> Optional[str]:
    """Query for transcription result."""
    # Result type 4 = plain text
    query_url = f"{QUERY_URL}?taskId={task_id}&resultType=4"
    
    for attempt in range(max_attempts):
        try:
            async with session.get(query_url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    code = result.get("code")
                    
                    if code == 11000:
                        # Success
                        text = result.get("result", "")
                        logger.info(f"Transcription complete: {text[:100]}...")
                        return text
                    elif code == 11001:
                        # Still processing
                        logger.debug(f"Transcription in progress, attempt {attempt + 1}/{max_attempts}")
                        await asyncio.sleep(poll_interval)
                        continue
                    else:
                        logger.error(f"Transcription error: {result.get('msg')}")
                        return None
                else:
                    logger.error(f"Query request failed: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error querying transcription: {e}")
            return None
    
    logger.error("Transcription timed out")
    return None


async def transcribe_telegram_voice(bot, file_id: str) -> Optional[str]:
    """
    Download and transcribe a Telegram voice message.
    
    Args:
        bot: Telegram bot instance
        file_id: Telegram file ID of the voice message
    
    Returns:
        Transcribed text or None if failed
    """
    import tempfile
    import os
    
    try:
        # Get file from Telegram
        file = await bot.get_file(file_id)
        
        # Download to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_file:
            temp_path = temp_file.name
            await file.download_to_drive(temp_path)
        
        logger.info(f"Downloaded voice message to {temp_path}")
        
        # Transcribe
        text = await transcribe_audio(temp_path, lang="ru")
        
        # Clean up
        try:
            os.unlink(temp_path)
        except:
            pass
        
        return text
        
    except Exception as e:
        logger.error(f"Error transcribing Telegram voice: {e}")
        return None


