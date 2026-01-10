"""LLM parser for user messages with JSON mode and model cascade."""
import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from openai import AsyncOpenAI
from pydantic import ValidationError

from schemas.llm_schema import LLMResponse, LLMResponseData
from llm.prompts import build_system_prompt, build_user_prompt
from utils.dates import now_in_timezone

logger = logging.getLogger(__name__)

# Initialize async OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Model cascade configuration
PRIMARY_MODEL = "gpt-5-mini"
FALLBACK_MODEL = "gpt-5.1"

# Cache the system prompt for OpenAI prompt caching
_CACHED_SYSTEM_PROMPT = None


def get_cached_system_prompt() -> str:
    """Get cached system prompt for prompt caching optimization."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is None:
        _CACHED_SYSTEM_PROMPT = build_system_prompt()
    return _CACHED_SYSTEM_PROMPT


async def _call_llm_json_mode(
    model: str,
    system_prompt: str,
    user_prompt: str
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Call LLM with JSON mode (async).
    Uses json_object response format + Pydantic validation.
    
    Returns:
        Tuple of (parsed_json, error_message)
    """
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        logger.info(f"[{model}] LLM raw response: {content}")
        
        # Parse JSON
        json_data = json.loads(content)
        return json_data, None
        
    except json.JSONDecodeError as e:
        error_msg = f"JSON parse error: {str(e)}"
        logger.error(f"[{model}] {error_msg}")
        return None, error_msg
        
    except Exception as e:
        error_msg = f"API error: {str(e)}"
        logger.error(f"[{model}] {error_msg}")
        return None, error_msg


def _validate_and_convert(json_data: dict) -> Tuple[Optional[LLMResponse], Optional[str]]:
    """
    Validate JSON with Pydantic and convert to LLMResponse.
    
    Returns:
        Tuple of (LLMResponse, error_message)
    """
    try:
        llm_response = LLMResponse(**json_data)
        return llm_response, None
    except ValidationError as e:
        error_msg = f"Validation error: {str(e)}"
        logger.error(error_msg)
        return None, error_msg


def _is_valid_response(response: LLMResponse) -> bool:
    """
    Check if response is valid and not a fallback/unknown.
    Used to decide whether to try fallback model.
    """
    # Consider response invalid if:
    # 1. Intent is unknown with low confidence
    # 2. Has errors
    if response.intent == "unknown" and response.confidence < 0.5:
        return False
    if response.errors and len(response.errors) > 0:
        return False
    return True


async def parse_message(
    user_message: str,
    accounts: List[Dict],
    default_account_name: Optional[str] = None,
    user_timezone: str = "Europe/London"
) -> LLMResponse:
    """
    Parse user message using LLM with model cascade (async).
    
    Primary model: gpt-4o-mini (cheap, fast)
    Fallback model: gpt-4o (more capable)
    
    Args:
        user_message: User's message
        accounts: List of account dicts with keys: name, currency, balance
        default_account_name: Name of default account
        user_timezone: User's timezone string
    
    Returns:
        LLMResponse object
    """
    current_datetime = now_in_timezone(user_timezone)
    
    # Use cached system prompt for prompt caching
    system_prompt = get_cached_system_prompt()
    user_prompt = build_user_prompt(
        user_message,
        accounts,
        default_account_name,
        current_datetime
    )
    
    # Try primary model first
    logger.info(f"Trying primary model: {PRIMARY_MODEL}")
    json_data, error = await _call_llm_json_mode(
        PRIMARY_MODEL, system_prompt, user_prompt
    )
    
    if json_data:
        response, validation_error = _validate_and_convert(json_data)
        if response and _is_valid_response(response):
            logger.info(f"[{PRIMARY_MODEL}] Success: intent={response.intent}, confidence={response.confidence}")
            return response
        elif response:
            logger.warning(f"[{PRIMARY_MODEL}] Low quality response, trying fallback")
        else:
            logger.warning(f"[{PRIMARY_MODEL}] Validation failed: {validation_error}")
    else:
        logger.warning(f"[{PRIMARY_MODEL}] Failed: {error}")
    
    # Try fallback model
    logger.info(f"Trying fallback model: {FALLBACK_MODEL}")
    json_data, error = await _call_llm_json_mode(
        FALLBACK_MODEL, system_prompt, user_prompt
    )
    
    if json_data:
        response, validation_error = _validate_and_convert(json_data)
        if response:
            logger.info(f"[{FALLBACK_MODEL}] Success: intent={response.intent}, confidence={response.confidence}")
            return response
        else:
            logger.error(f"[{FALLBACK_MODEL}] Validation failed: {validation_error}")
    else:
        logger.error(f"[{FALLBACK_MODEL}] Failed: {error}")
    
    # Both models failed - return unknown
    logger.error("Both models failed, returning unknown intent")
    return LLMResponse(
        intent="unknown",
        confidence=0.0,
        data=LLMResponseData(),
        errors=[error or "All models failed"]
    )


