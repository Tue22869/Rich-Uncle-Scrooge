"""Usage / cost analytics service.

Single point for writing UsageEvent rows. Computes USD cost in micros
(1 USD = 1_000_000 micros) using PRICE_TABLE — integer math only, no drift.

To update prices: edit PRICE_TABLE in one place.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from sqlalchemy.orm import Session

from db.models import UsageEvent, UsageEventKind

logger = logging.getLogger(__name__)

# USD per 1M tokens for chat models, USD per minute for whisper.
# Values reflect OpenAI public pricing for the gpt-5 family on 2026-05.
# Cached input is billed at 50% of the regular input price.
PRICE_TABLE = {
    "gpt-5-mini":  {"input": 0.25,  "cached_input": 0.125, "output": 2.00},
    "gpt-5.1":     {"input": 1.25,  "cached_input": 0.625, "output": 10.00},
    "whisper-1":   {"per_minute": 0.006},
}

# Default RUB exchange rate for display. Override via env in cost helpers if needed.
USD_TO_RUB_DEFAULT = 90.0

# 1 USD = 1_000_000 micros. Use integers everywhere; convert at the edges.
_MICRO = 1_000_000


def compute_cost_micro(
    model: Optional[str],
    *,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    audio_seconds: int = 0,
) -> int:
    """Compute the USD cost of a single API call, in micros (integer)."""
    if not model:
        return 0

    pricing = PRICE_TABLE.get(model)
    if pricing is None:
        logger.warning(f"compute_cost_micro: unknown model {model!r}; cost set to 0")
        return 0

    total_usd = 0.0

    if "per_minute" in pricing:
        minutes = audio_seconds / 60.0
        total_usd += minutes * pricing["per_minute"]
    else:
        # Chat model: input is split into "fresh" and "cached" parts.
        fresh_input = max(0, input_tokens - cached_input_tokens)
        total_usd += (fresh_input / 1_000_000) * pricing["input"]
        total_usd += (cached_input_tokens / 1_000_000) * pricing.get(
            "cached_input", pricing["input"] * 0.5
        )
        total_usd += (output_tokens / 1_000_000) * pricing["output"]

    return int(math.ceil(total_usd * _MICRO))


def micro_to_usd(micro: int) -> float:
    """Convert micro-dollars to USD."""
    return micro / _MICRO


def micro_to_rub(micro: int, rate: float = USD_TO_RUB_DEFAULT) -> float:
    """Convert micro-dollars to RUB."""
    return micro_to_usd(micro) * rate


def log_event(
    db: Session,
    *,
    user_id: Optional[int],
    kind: str,
    model: Optional[str] = None,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    audio_seconds: int = 0,
    meta: Optional[dict] = None,
    commit: bool = True,
) -> Optional[UsageEvent]:
    """Persist a UsageEvent row. Returns None if logging fails (never raises).

    Caller MUST own the Session lifecycle. We commit by default so events survive
    even if the surrounding business logic later rolls back, but you can pass
    `commit=False` to batch with the caller's transaction.

    user_id is the internal users.id, NOT tg_user_id. None is allowed for very
    early events (e.g. anonymous /start before User row exists) — those rows are
    skipped to keep FK integrity.
    """
    if user_id is None:
        return None

    try:
        cost_micro = compute_cost_micro(
            model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            audio_seconds=audio_seconds,
        )
        event = UsageEvent(
            user_id=user_id,
            kind=kind,
            model=model,
            input_tokens=input_tokens or None,
            cached_input_tokens=cached_input_tokens or None,
            output_tokens=output_tokens or None,
            audio_seconds=audio_seconds or None,
            cost_usd_micro=cost_micro or None,
            meta=meta,
        )
        db.add(event)
        if commit:
            db.commit()
            db.refresh(event)
        return event
    except Exception as e:
        # Analytics must never break the user-facing flow.
        logger.error(f"log_event failed (kind={kind}, user_id={user_id}): {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def log_event_safe(*args, **kwargs) -> None:
    """Fire-and-forget convenience wrapper. Same signature as log_event."""
    log_event(*args, **kwargs)


def log_first_feature(db: Session, user_id: int, feature: str) -> bool:
    """Mark a feature_first event idempotently. Returns True if it was newly recorded.

    Used for the aha-moment funnel — e.g. "first time a user opened a report".
    Looks up by (user_id, kind=feature_first, meta.feature).
    """
    existing = (
        db.query(UsageEvent)
        .filter(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == UsageEventKind.FEATURE_FIRST,
            UsageEvent.meta["feature"].as_string() == feature,
        )
        .first()
    )
    if existing:
        return False

    log_event(
        db,
        user_id=user_id,
        kind=UsageEventKind.FEATURE_FIRST,
        meta={"feature": feature},
    )
    return True
