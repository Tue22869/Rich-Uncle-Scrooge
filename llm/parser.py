"""LLM parser for user messages with JSON mode and model cascade."""
import os
import json
import logging
from typing import List, Dict, Optional, Tuple

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.orm import Session

from schemas.llm_schema import LLMResponse, LLMResponseData
from llm.prompts import build_system_prompt, build_user_prompt, build_analysis_system_prompt
from utils.dates import now_in_timezone

logger = logging.getLogger(__name__)

load_dotenv()

# Initialize async OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Model cascade configuration
PRIMARY_MODEL = "gpt-5-mini"
FALLBACK_MODEL = "gpt-5.1"
ANALYSIS_MODEL = "gpt-5.1"  # Smarter model for free-form report/insight analysis

# Some OpenAI models reject any temperature other than the default (1) and
# return 400 invalid_request_error. Listed here so we omit the param entirely
# for them — passing the default explicitly still trips the same check.
MODELS_WITHOUT_TEMPERATURE = {"gpt-5-mini"}

# Cache the system prompt for OpenAI prompt caching
_CACHED_SYSTEM_PROMPT = None


def get_cached_system_prompt() -> str:
    """Get cached system prompt for prompt caching optimization."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is None:
        _CACHED_SYSTEM_PROMPT = build_system_prompt()
    return _CACHED_SYSTEM_PROMPT


def _extract_usage(response) -> dict:
    """Pull token counts off an OpenAI response in a defensive way.

    Different model SKUs expose `prompt_tokens_details` slightly differently;
    we degrade gracefully to zeros so analytics never blows up the call.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}

    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0

    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0

    return {
        "input_tokens": prompt_tokens,
        "cached_input_tokens": cached,
        "output_tokens": completion_tokens,
    }


async def _call_llm_json_mode(
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> Tuple[Optional[dict], Optional[str], dict]:
    """
    Call LLM with JSON mode (async).
    Uses json_object response format + Pydantic validation.

    Returns:
        Tuple of (parsed_json, error_message, usage_dict).
        usage_dict always has input_tokens / cached_input_tokens / output_tokens keys.
    """
    empty_usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if model not in MODELS_WITHOUT_TEMPERATURE:
        kwargs["temperature"] = 0
    try:
        response = await client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        logger.info(f"[{model}] LLM raw response: {content}")

        usage = _extract_usage(response)

        # Parse JSON
        json_data = json.loads(content)
        return json_data, None, usage

    except json.JSONDecodeError as e:
        error_msg = f"JSON parse error: {str(e)}"
        logger.error(f"[{model}] {error_msg}")
        return None, error_msg, empty_usage

    except Exception as e:
        error_msg = f"API error: {str(e)}"
        logger.error(f"[{model}] {error_msg}")
        return None, error_msg, empty_usage


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


async def generate_analysis(
    data_str: str,
    user_question: str = "",
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """
    Second-pass LLM call: given aggregated financial data as text,
    generate a natural-language analysis in Russian.

    Returns the analysis string, or None on failure (caller should fall back to template).

    If `db` and `user_id` are passed, a usage event is recorded with token counts
    and computed cost. Both must be set; otherwise tracking is silently skipped.
    """
    system_prompt = build_analysis_system_prompt()
    user_content = f"Данные:\n{data_str}"
    if user_question:
        user_content += f"\n\nВопрос пользователя: {user_question}"

    try:
        response = await client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
        )

        # Best-effort analytics — never let it break the call.
        if db is not None and user_id is not None:
            try:
                from services.analytics import log_event
                from db.models import UsageEventKind
                usage = _extract_usage(response)
                log_event(
                    db,
                    user_id=user_id,
                    kind=UsageEventKind.ANALYSIS_LLM,
                    model=ANALYSIS_MODEL,
                    **usage,
                    meta={"has_question": bool(user_question)},
                )
            except Exception as e:
                logger.warning(f"analysis usage logging failed: {e}")

        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"generate_analysis failed: {e}")
        return None


def _log_parse_usage(
    db: Optional[Session],
    user_id: Optional[int],
    model: str,
    usage: dict,
    intent: Optional[str],
    confidence: Optional[float],
) -> None:
    """Best-effort usage logging for parse_message cascade."""
    if db is None or user_id is None:
        return
    try:
        from services.analytics import log_event
        from db.models import UsageEventKind
        log_event(
            db,
            user_id=user_id,
            kind=UsageEventKind.PARSE_LLM,
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            cached_input_tokens=usage.get("cached_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            meta={"intent": intent, "confidence": confidence},
        )
    except Exception as e:
        logger.warning(f"parse usage logging failed: {e}")


async def parse_message(
    user_message: str,
    accounts: List[Dict],
    default_account_name: Optional[str] = None,
    user_timezone: str = "Europe/London",
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> LLMResponse:
    """
    Parse user message using LLM with model cascade (async).

    Primary model: PRIMARY_MODEL (cheap, fast)
    Fallback model: FALLBACK_MODEL (more capable)

    Args:
        user_message: User's message
        accounts: List of account dicts with keys: name, currency, balance
        default_account_name: Name of default account
        user_timezone: User's timezone string
        db: Optional Session for usage analytics. If both db and user_id are
            provided, each LLM call (including fallback) records a usage event.
        user_id: Internal users.id (NOT tg_user_id) for usage attribution.

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
    json_data, error, primary_usage = await _call_llm_json_mode(
        PRIMARY_MODEL, system_prompt, user_prompt
    )

    if json_data:
        response, validation_error = _validate_and_convert(json_data)
        if response and _is_valid_response(response):
            logger.info(f"[{PRIMARY_MODEL}] Success: intent={response.intent}, confidence={response.confidence}")
            _log_parse_usage(db, user_id, PRIMARY_MODEL, primary_usage,
                             response.intent, response.confidence)
            return response
        elif response:
            logger.warning(f"[{PRIMARY_MODEL}] Low quality response, trying fallback")
            # Still bill the primary call — we paid for it.
            _log_parse_usage(db, user_id, PRIMARY_MODEL, primary_usage,
                             response.intent, response.confidence)
        else:
            logger.warning(f"[{PRIMARY_MODEL}] Validation failed: {validation_error}")
            _log_parse_usage(db, user_id, PRIMARY_MODEL, primary_usage, None, None)
    else:
        logger.warning(f"[{PRIMARY_MODEL}] Failed: {error}")
        _log_parse_usage(db, user_id, PRIMARY_MODEL, primary_usage, None, None)

    # Try fallback model
    logger.info(f"Trying fallback model: {FALLBACK_MODEL}")
    json_data, error, fallback_usage = await _call_llm_json_mode(
        FALLBACK_MODEL, system_prompt, user_prompt
    )

    if json_data:
        response, validation_error = _validate_and_convert(json_data)
        if response:
            logger.info(f"[{FALLBACK_MODEL}] Success: intent={response.intent}, confidence={response.confidence}")
            _log_parse_usage(db, user_id, FALLBACK_MODEL, fallback_usage,
                             response.intent, response.confidence)
            return response
        else:
            logger.error(f"[{FALLBACK_MODEL}] Validation failed: {validation_error}")
            _log_parse_usage(db, user_id, FALLBACK_MODEL, fallback_usage, None, None)
    else:
        logger.error(f"[{FALLBACK_MODEL}] Failed: {error}")
        _log_parse_usage(db, user_id, FALLBACK_MODEL, fallback_usage, None, None)

    # Both models failed - return unknown
    logger.error("Both models failed, returning unknown intent")
    return LLMResponse(
        intent="unknown",
        confidence=0.0,
        data=LLMResponseData(),
        errors=[error or "All models failed"]
    )


