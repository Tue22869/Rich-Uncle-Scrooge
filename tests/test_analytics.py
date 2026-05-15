"""Tests for services/analytics.py — cost computation and event logging."""
import pytest

from db.models import User, UsageEvent, UsageEventKind
from db.session import SessionLocal, init_db
from services.analytics import (
    PRICE_TABLE,
    compute_cost_micro,
    log_event,
    log_first_feature,
    micro_to_rub,
    micro_to_usd,
)


# ---------- Fixtures ----------

@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        from sqlalchemy import text
        for table in ("usage_events", "subscriptions", "users"):
            session.execute(text(f"DELETE FROM {table}"))
        session.commit()
        session.close()


@pytest.fixture
def user(db):
    u = User(tg_user_id=999)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------- compute_cost_micro ----------

def test_cost_unknown_model_is_zero():
    assert compute_cost_micro("definitely-not-a-real-model", input_tokens=1000) == 0


def test_cost_no_model_is_zero():
    assert compute_cost_micro(None, output_tokens=500) == 0


def test_cost_gpt5_mini_fresh_input_only():
    # 1M input tokens at $0.25/M = $0.25 = 250_000 micros
    cost = compute_cost_micro("gpt-5-mini", input_tokens=1_000_000)
    assert cost == 250_000


def test_cost_gpt5_mini_cached_input_half_price():
    # 1M cached input at $0.125/M = $0.125 = 125_000 micros
    cost = compute_cost_micro(
        "gpt-5-mini",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
    )
    assert cost == 125_000


def test_cost_gpt5_mini_mixed_input():
    # 500k fresh + 500k cached + 100k output
    # = (0.5 * 0.25) + (0.5 * 0.125) + (0.1 * 2.00) USD
    # = 0.125 + 0.0625 + 0.20 = 0.3875 USD = 387_500 micros
    cost = compute_cost_micro(
        "gpt-5-mini",
        input_tokens=1_000_000,
        cached_input_tokens=500_000,
        output_tokens=100_000,
    )
    assert cost == 387_500


def test_cost_gpt51_more_expensive_than_mini():
    args = {"input_tokens": 1000, "output_tokens": 500}
    mini = compute_cost_micro("gpt-5-mini", **args)
    full = compute_cost_micro("gpt-5.1", **args)
    assert full > mini * 4   # 5.1 is ~5x mini


def test_cost_whisper_per_minute():
    # 60 seconds = 1 minute = $0.006 = 6_000 micros (rounded up by ceil)
    cost = compute_cost_micro("whisper-1", audio_seconds=60)
    assert cost == 6_000


def test_cost_whisper_fractional_seconds():
    # 30 seconds = 0.5 min = $0.003 = 3_000 micros
    cost = compute_cost_micro("whisper-1", audio_seconds=30)
    assert cost == 3_000


def test_cost_whisper_zero_audio():
    assert compute_cost_micro("whisper-1", audio_seconds=0) == 0


def test_micro_to_usd_and_rub_conversions():
    assert micro_to_usd(500_000) == 0.5
    assert micro_to_rub(1_000_000, rate=90.0) == 90.0


def test_price_table_has_required_models():
    # Don't accidentally drop a model from the table.
    assert "gpt-5-mini" in PRICE_TABLE
    assert "gpt-5.1" in PRICE_TABLE
    assert "whisper-1" in PRICE_TABLE


# ---------- log_event ----------

def test_log_event_inserts_row(db, user):
    ev = log_event(
        db,
        user_id=user.id,
        kind=UsageEventKind.PARSE_LLM,
        model="gpt-5-mini",
        input_tokens=2000,
        cached_input_tokens=1500,
        output_tokens=300,
        meta={"intent": "expense"},
    )
    assert ev is not None
    assert ev.id is not None
    assert ev.cost_usd_micro > 0
    assert ev.meta == {"intent": "expense"}


def test_log_event_no_user_id_skipped(db):
    ev = log_event(db, user_id=None, kind=UsageEventKind.MESSAGE_IN)
    assert ev is None
    assert db.query(UsageEvent).count() == 0


def test_log_event_swallows_db_errors(db, user, monkeypatch):
    """A broken add() must NOT propagate — analytics is best-effort."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated db failure")
    monkeypatch.setattr(db, "add", boom)

    ev = log_event(db, user_id=user.id, kind=UsageEventKind.MESSAGE_IN)
    assert ev is None  # logged, not raised


def test_log_event_zero_cost_event(db, user):
    ev = log_event(db, user_id=user.id, kind=UsageEventKind.MESSAGE_IN)
    assert ev is not None
    assert ev.model is None
    assert ev.input_tokens is None
    assert ev.cost_usd_micro is None


def test_log_event_whisper_records_audio_seconds(db, user):
    ev = log_event(
        db,
        user_id=user.id,
        kind=UsageEventKind.WHISPER,
        model="whisper-1",
        audio_seconds=12,
    )
    assert ev.audio_seconds == 12
    assert ev.cost_usd_micro == compute_cost_micro("whisper-1", audio_seconds=12)


# ---------- log_first_feature ----------

def test_first_feature_returns_true_first_time(db, user):
    assert log_first_feature(db, user.id, "report") is True


def test_first_feature_idempotent(db, user):
    log_first_feature(db, user.id, "report")
    # second call: same feature, must not insert a duplicate
    assert log_first_feature(db, user.id, "report") is False
    count = (
        db.query(UsageEvent)
        .filter(UsageEvent.kind == UsageEventKind.FEATURE_FIRST)
        .count()
    )
    assert count == 1


def test_first_feature_distinct_features(db, user):
    assert log_first_feature(db, user.id, "report") is True
    assert log_first_feature(db, user.id, "analysis") is True
    assert log_first_feature(db, user.id, "report") is False
