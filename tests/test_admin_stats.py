"""Tests for /admin_stats aggregation queries and access control."""
import os
from datetime import datetime, timedelta

import pytest

from bot.admin import (
    _admin_ids,
    _aha_funnel,
    _conversion_trial_to_paid,
    _cost_since,
    _count_distinct_users_since,
    _count_new_users_since,
    _count_subscriptions_by_status,
    _revenue_since,
    _top_spending_users,
    _whisper_load,
    is_admin,
    render_admin_stats,
)
from db.models import (
    Subscription,
    SubscriptionPlan,
    SubscriptionProvider,
    SubscriptionStatus,
    UsageEvent,
    UsageEventKind,
    User,
)
from db.session import SessionLocal, init_db


@pytest.fixture
def db():
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        from sqlalchemy import text
        for table in ("usage_events", "subscriptions", "users"):
            s.execute(text(f"DELETE FROM {table}"))
        s.commit()
        s.close()


def _make_user(db, tg_user_id, days_ago=0):
    u = User(tg_user_id=tg_user_id,
             created_at=datetime.utcnow() - timedelta(days=days_ago))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_event(db, user, kind, *, hours_ago=0, cost_micro=None, meta=None,
                audio_seconds=None):
    ev = UsageEvent(
        user_id=user.id,
        kind=kind,
        created_at=datetime.utcnow() - timedelta(hours=hours_ago),
        cost_usd_micro=cost_micro,
        audio_seconds=audio_seconds,
        meta=meta,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# ---------- admin access ----------

def test_admin_ids_empty_env(monkeypatch):
    monkeypatch.setenv("ADMIN_TG_IDS", "")
    assert _admin_ids() == set()


def test_admin_ids_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("ADMIN_TG_IDS", "123, 456 ,789")
    assert _admin_ids() == {123, 456, 789}


def test_admin_ids_skips_invalid(monkeypatch):
    monkeypatch.setenv("ADMIN_TG_IDS", "123,abc,456")
    assert _admin_ids() == {123, 456}


def test_admin_ids_handles_semicolons(monkeypatch):
    monkeypatch.setenv("ADMIN_TG_IDS", "10;20;30")
    assert _admin_ids() == {10, 20, 30}


def test_is_admin_true_when_in_list(monkeypatch):
    monkeypatch.setenv("ADMIN_TG_IDS", "42")
    assert is_admin(42)
    assert not is_admin(43)


# ---------- DAU/MAU counters ----------

def test_dau_counts_distinct_active_users(db):
    u1 = _make_user(db, tg_user_id=1)
    u2 = _make_user(db, tg_user_id=2)
    _make_event(db, u1, UsageEventKind.MESSAGE_IN, hours_ago=2)
    _make_event(db, u1, UsageEventKind.MESSAGE_IN, hours_ago=3)  # same user
    _make_event(db, u2, UsageEventKind.MESSAGE_IN, hours_ago=10)
    yesterday = datetime.utcnow() - timedelta(days=1)
    assert _count_distinct_users_since(db, yesterday) == 2


def test_dau_excludes_old_events(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.MESSAGE_IN, hours_ago=48)  # 2 days ago
    yesterday = datetime.utcnow() - timedelta(days=1)
    assert _count_distinct_users_since(db, yesterday) == 0


def test_new_users_count(db):
    _make_user(db, tg_user_id=1, days_ago=0)
    _make_user(db, tg_user_id=2, days_ago=0)
    _make_user(db, tg_user_id=3, days_ago=10)  # old
    since = datetime.utcnow() - timedelta(days=1)
    assert _count_new_users_since(db, since) == 2


# ---------- Subscriptions ----------

def _make_sub(db, user, provider, days_left=30):
    now = datetime.utcnow()
    sub = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        provider=provider,
        paid_at=now,
        expires_at=now + timedelta(days=days_left),
    )
    db.add(sub)
    db.commit()
    return sub


def test_subscription_status_counts(db):
    u1 = _make_user(db, tg_user_id=1)
    u2 = _make_user(db, tg_user_id=2)
    u3 = _make_user(db, tg_user_id=3)
    _make_sub(db, u1, SubscriptionProvider.STARS)
    _make_sub(db, u2, SubscriptionProvider.YOOKASSA)
    _make_sub(db, u3, SubscriptionProvider.TRIAL)
    total, paid, trial = _count_subscriptions_by_status(db, datetime.utcnow())
    assert total == 3
    assert paid == 2
    assert trial == 1


def test_subscription_excludes_expired(db):
    u = _make_user(db, tg_user_id=1)
    _make_sub(db, u, SubscriptionProvider.STARS, days_left=-1)
    total, paid, trial = _count_subscriptions_by_status(db, datetime.utcnow())
    assert total == 0


# ---------- Conversion ----------

def test_conversion_calculation(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.TRIAL_STARTED, hours_ago=240)
    _make_event(db, u, UsageEventKind.TRIAL_STARTED, hours_ago=200)
    _make_event(db, u, UsageEventKind.SUBSCRIPTION_PAID, hours_ago=100)
    trials, paid, pct = _conversion_trial_to_paid(db, days=30)
    assert trials == 2
    assert paid == 1
    assert pct == 50.0


def test_conversion_handles_zero_trials(db):
    trials, paid, pct = _conversion_trial_to_paid(db, days=30)
    assert trials == 0
    assert paid == 0
    assert pct == 0.0


# ---------- Revenue ----------

def test_revenue_splits_by_provider(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.SUBSCRIPTION_PAID, hours_ago=10,
                meta={"provider": SubscriptionProvider.YOOKASSA, "net_rub": 172})
    _make_event(db, u, UsageEventKind.SUBSCRIPTION_PAID, hours_ago=20,
                meta={"provider": SubscriptionProvider.STARS, "net_rub": 82})
    _make_event(db, u, UsageEventKind.SUBSCRIPTION_PAID, hours_ago=30,
                meta={"provider": SubscriptionProvider.STARS, "net_rub": 82})
    since = datetime.utcnow() - timedelta(days=30)
    yk, stars = _revenue_since(db, since)
    assert yk == 172
    assert stars == 164


def test_revenue_ignores_events_without_meta(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.SUBSCRIPTION_PAID, hours_ago=10, meta=None)
    since = datetime.utcnow() - timedelta(days=30)
    yk, stars = _revenue_since(db, since)
    assert yk == 0 and stars == 0


# ---------- Cost ----------

def test_cost_aggregates_by_kind(db):
    u = _make_user(db, tg_user_id=1)
    # 1_000_000 micros = $1 = 90 ₽
    _make_event(db, u, UsageEventKind.PARSE_LLM, cost_micro=1_000_000, hours_ago=1)
    _make_event(db, u, UsageEventKind.PARSE_LLM, cost_micro=2_000_000, hours_ago=2)
    _make_event(db, u, UsageEventKind.WHISPER, cost_micro=500_000, hours_ago=3)
    since = datetime.utcnow() - timedelta(days=30)
    out = _cost_since(db, since, usd_rate=90.0)
    assert out[UsageEventKind.PARSE_LLM] == pytest.approx(270.0)  # 3 USD * 90
    assert out[UsageEventKind.WHISPER] == pytest.approx(45.0)


def test_cost_ignores_events_without_cost(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.MESSAGE_IN, hours_ago=1)  # no cost
    since = datetime.utcnow() - timedelta(days=30)
    out = _cost_since(db, since)
    assert out == {}


# ---------- Top spenders ----------

def test_top_spenders_ordering(db):
    u1 = _make_user(db, tg_user_id=10)
    u2 = _make_user(db, tg_user_id=20)
    u3 = _make_user(db, tg_user_id=30)
    _make_event(db, u1, UsageEventKind.PARSE_LLM, cost_micro=100_000, hours_ago=1)
    _make_event(db, u2, UsageEventKind.PARSE_LLM, cost_micro=500_000, hours_ago=1)
    _make_event(db, u3, UsageEventKind.PARSE_LLM, cost_micro=200_000, hours_ago=1)
    since = datetime.utcnow() - timedelta(days=30)
    top = _top_spending_users(db, since)
    # Order: u2 (500k) > u3 (200k) > u1 (100k)
    tgs = [r[0] for r in top]
    assert tgs == [20, 30, 10]


# ---------- Funnel ----------

def test_aha_funnel_counts(db):
    u1 = _make_user(db, tg_user_id=1, days_ago=2)
    u2 = _make_user(db, tg_user_id=2, days_ago=2)
    _make_event(db, u1, UsageEventKind.PARSE_LLM, hours_ago=10)
    _make_event(db, u1, UsageEventKind.REPORT_VIEW, hours_ago=5)
    _make_event(db, u2, UsageEventKind.PARSE_LLM, hours_ago=12)
    funnel = _aha_funnel(db, days=7)
    funnel_dict = dict(funnel)
    assert funnel_dict["/start (новые)"] == 2
    assert funnel_dict["первая запись (parse_llm)"] == 2
    assert funnel_dict["первый отчёт"] == 1


def test_aha_funnel_empty_when_no_new_users(db):
    funnel = _aha_funnel(db, days=7)
    # Returns a non-empty list with a zero starting count
    assert funnel == [("/start", 0)]


# ---------- Whisper load ----------

def test_whisper_load_aggregates(db):
    u = _make_user(db, tg_user_id=1)
    _make_event(db, u, UsageEventKind.WHISPER, audio_seconds=10, hours_ago=1)
    _make_event(db, u, UsageEventKind.WHISPER, audio_seconds=20, hours_ago=2)
    since = datetime.utcnow() - timedelta(days=30)
    minutes, avg, voice_users = _whisper_load(db, since)
    assert minutes == pytest.approx(30 / 60.0)
    assert avg == pytest.approx(15.0)
    assert voice_users == 1


# ---------- End-to-end render ----------

def test_render_admin_stats_smoke(db):
    """Just ensure it runs and produces non-empty text on a real DB."""
    u = _make_user(db, tg_user_id=42)
    _make_event(db, u, UsageEventKind.MESSAGE_IN, hours_ago=1)
    _make_event(db, u, UsageEventKind.PARSE_LLM, cost_micro=100_000, hours_ago=1)
    out = render_admin_stats(db)
    assert "Дядя Скрудж" in out
    assert "DAU" in out
    assert "Маржа" in out


def test_render_admin_stats_on_empty_db(db):
    """Must not crash on a fresh DB with zero events."""
    out = render_admin_stats(db)
    assert "DAU" in out
