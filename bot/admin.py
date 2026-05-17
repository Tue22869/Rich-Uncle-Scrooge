"""Admin-only /admin_stats command.

Access controlled by ADMIN_USER_IDS env var (comma-separated Telegram user IDs).
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Set

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from db.models import (
    UsageEvent, UsageEventKind, User, Subscription,
    SubscriptionStatus, SubscriptionProvider,
)
from db.session import SessionLocal
from services.analytics import micro_to_rub

logger = logging.getLogger(__name__)


def _admin_ids() -> Set[int]:
    raw = os.getenv("ADMIN_USER_IDS", "").strip()
    if not raw:
        return set()
    out: Set[int] = set()
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            logger.warning(f"ADMIN_USER_IDS: skipping non-int token {token!r}")
    return out


def is_admin(tg_user_id: int) -> bool:
    return tg_user_id in _admin_ids()


# Telegram legacy Markdown treats `_` `*` `` ` `` `[` as entity delimiters.
# Any dynamic string interpolated into a Markdown message can contain these
# and break parsing. Escape before formatting.
def _md_escape(s) -> str:
    """Escape Telegram legacy Markdown special chars in dynamic text."""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def _count_distinct_users_since(db: Session, since: datetime) -> int:
    return (
        db.query(func.count(distinct(UsageEvent.user_id)))
        .filter(UsageEvent.created_at >= since)
        .scalar() or 0
    )


def _count_new_users_since(db: Session, since: datetime) -> int:
    return (
        db.query(func.count(User.id))
        .filter(User.created_at >= since)
        .scalar() or 0
    )


def _count_subscriptions_by_status(db: Session, now: datetime):
    """Return (active_total, active_paid, active_trial)."""
    rows = (
        db.query(Subscription.provider, func.count(Subscription.id))
        .filter(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
        .group_by(Subscription.provider)
        .all()
    )
    counts = {provider: cnt for provider, cnt in rows}
    trial = counts.get(SubscriptionProvider.TRIAL, 0)
    paid = sum(c for p, c in counts.items() if p != SubscriptionProvider.TRIAL)
    return paid + trial, paid, trial


def _conversion_trial_to_paid(db: Session, days: int = 30):
    """Returns (trials_started, paid_after_trial, conversion_pct)."""
    since = datetime.utcnow() - timedelta(days=days)
    trials = (
        db.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.kind == UsageEventKind.TRIAL_STARTED,
            UsageEvent.created_at >= since,
        )
        .scalar() or 0
    )
    paid = (
        db.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.kind == UsageEventKind.SUBSCRIPTION_PAID,
            UsageEvent.created_at >= since,
        )
        .scalar() or 0
    )
    pct = (paid / trials * 100.0) if trials else 0.0
    return trials, paid, pct


def _revenue_since(db: Session, since: datetime, usd_rate: float = 90.0):
    """Sum net_rub from subscription_paid events. Returns (yookassa_rub, stars_rub)."""
    rows = (
        db.query(UsageEvent.meta)
        .filter(
            UsageEvent.kind == UsageEventKind.SUBSCRIPTION_PAID,
            UsageEvent.created_at >= since,
        )
        .all()
    )
    yookassa, stars = 0, 0
    for (meta,) in rows:
        if not meta:
            continue
        provider = meta.get("provider")
        net = meta.get("net_rub") or 0
        if provider == SubscriptionProvider.STARS:
            stars += int(net)
        elif provider == SubscriptionProvider.YOOKASSA:
            yookassa += int(net)
    return yookassa, stars


def _cost_since(db: Session, since: datetime, usd_rate: float = 90.0):
    """Returns dict {kind: rub_cost} for cost-bearing kinds."""
    rows = (
        db.query(UsageEvent.kind, func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0))
        .filter(
            UsageEvent.created_at >= since,
            UsageEvent.cost_usd_micro.isnot(None),
        )
        .group_by(UsageEvent.kind)
        .all()
    )
    out = {}
    for kind, total_micro in rows:
        out[kind] = micro_to_rub(int(total_micro), rate=usd_rate)
    return out


def _top_spending_users(db: Session, since: datetime, limit: int = 5,
                       usd_rate: float = 90.0):
    """Returns [(tg_user_id, rub_cost, voice_count, analysis_count), ...]"""
    cost_rows = (
        db.query(
            UsageEvent.user_id,
            func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0).label("cost"),
        )
        .filter(
            UsageEvent.created_at >= since,
            UsageEvent.cost_usd_micro.isnot(None),
        )
        .group_by(UsageEvent.user_id)
        .order_by(func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0).desc())
        .limit(limit)
        .all()
    )
    out = []
    for user_id, cost in cost_rows:
        tg = (
            db.query(User.tg_user_id)
            .filter(User.id == user_id)
            .scalar()
        )
        voice_n = (
            db.query(func.count(UsageEvent.id))
            .filter(
                UsageEvent.user_id == user_id,
                UsageEvent.kind == UsageEventKind.WHISPER,
                UsageEvent.created_at >= since,
            )
            .scalar() or 0
        )
        analysis_n = (
            db.query(func.count(UsageEvent.id))
            .filter(
                UsageEvent.user_id == user_id,
                UsageEvent.kind == UsageEventKind.ANALYSIS_LLM,
                UsageEvent.created_at >= since,
            )
            .scalar() or 0
        )
        out.append((tg, micro_to_rub(int(cost), rate=usd_rate), voice_n, analysis_n))
    return out


def _aha_funnel(db: Session, days: int = 7):
    """Funnel for users registered in last `days`. Returns ordered list of (label, count)."""
    since = datetime.utcnow() - timedelta(days=days)
    new_users = (
        db.query(User.id)
        .filter(User.created_at >= since)
        .all()
    )
    new_ids = {u for (u,) in new_users}
    if not new_ids:
        return [("/start", 0)]

    def _count_kind(kind: str) -> int:
        return (
            db.query(func.count(distinct(UsageEvent.user_id)))
            .filter(
                UsageEvent.user_id.in_(new_ids),
                UsageEvent.kind == kind,
            )
            .scalar() or 0
        )

    return [
        ("/start (новые)", len(new_ids)),
        ("первая запись (parse_llm)", _count_kind(UsageEventKind.PARSE_LLM)),
        ("первый отчёт", _count_kind(UsageEventKind.REPORT_VIEW)),
        ("первый анализ", _count_kind(UsageEventKind.ANALYSIS_VIEW)),
        ("trial активирован", _count_kind(UsageEventKind.TRIAL_STARTED)),
        ("→ paid", _count_kind(UsageEventKind.SUBSCRIPTION_PAID)),
    ]


def _whisper_load(db: Session, since: datetime):
    """Returns (total_minutes, avg_seconds, voice_active_users)."""
    total_seconds, count = (
        db.query(
            func.coalesce(func.sum(UsageEvent.audio_seconds), 0),
            func.count(UsageEvent.id),
        )
        .filter(
            UsageEvent.kind == UsageEventKind.WHISPER,
            UsageEvent.created_at >= since,
        )
        .one()
    )
    voice_users = (
        db.query(func.count(distinct(UsageEvent.user_id)))
        .filter(
            UsageEvent.kind == UsageEventKind.WHISPER,
            UsageEvent.created_at >= since,
        )
        .scalar() or 0
    )
    total_minutes = (total_seconds or 0) / 60.0
    avg = (total_seconds / count) if count else 0.0
    return total_minutes, avg, voice_users


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_admin_stats(db: Session, *, usd_rate: float = 90.0) -> str:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    dau = _count_distinct_users_since(db, day_ago)
    wau = _count_distinct_users_since(db, week_ago)
    mau = _count_distinct_users_since(db, month_ago)
    new_today = _count_new_users_since(db, day_ago)
    new_month = _count_new_users_since(db, month_ago)

    active_total, active_paid, active_trial = _count_subscriptions_by_status(db, now)
    trials_30d, paid_30d, conv_pct = _conversion_trial_to_paid(db, days=30)

    yookassa_rub, stars_rub = _revenue_since(db, month_ago, usd_rate)
    revenue_total = yookassa_rub + stars_rub

    cost_by_kind = _cost_since(db, month_ago, usd_rate)
    cost_total = sum(cost_by_kind.values())
    margin = revenue_total - cost_total

    top_users = _top_spending_users(db, month_ago, limit=5, usd_rate=usd_rate)

    funnel = _aha_funnel(db, days=7)

    whisper_minutes, whisper_avg, voice_users = _whisper_load(db, month_ago)

    lines = []
    lines.append(f"📊 *Дядя Скрудж · admin · {now.strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")
    lines.append("*👥 Пользователи*")
    lines.append(f"  DAU: `{dau}`  WAU: `{wau}`  MAU: `{mau}`")
    lines.append(f"  Новых сегодня: `{new_today}` · за месяц: `{new_month}`")
    lines.append("")
    lines.append("*💳 Подписки*")
    lines.append(f"  Активных: `{active_total}` (paid: `{active_paid}` · trial: `{active_trial}`)")
    lines.append(f"  Конверсия trial→paid (30 дн): `{conv_pct:.1f}%`  "
                 f"(`{paid_30d}` оплат / `{trials_30d}` trial)")
    lines.append("")
    lines.append("*💰 Деньги · за 30 дней*")
    lines.append(f"  Доход: `{revenue_total:,.0f} ₽`  "
                 f"(YooKassa `{yookassa_rub:,.0f}` · Stars `{stars_rub:,.0f}`)")
    lines.append(f"  Расход OpenAI: `{cost_total:,.1f} ₽`")
    for kind_label, kind_value in [
        ("parse_llm",    "parse"),
        ("analysis_llm", "analysis"),
        ("digest_llm",   "digest"),
        ("whisper",      "whisper"),
    ]:
        v = cost_by_kind.get(kind_label, 0.0)
        lines.append(f"    {kind_value:<10} `{v:>7,.1f} ₽`")
    lines.append(f"  *Маржа: `{margin:,.0f} ₽`*")
    lines.append("")

    if top_users:
        lines.append("*🔥 Топ-5 по расходу OpenAI (30 дн)*")
        for tg, cost, voice_n, analysis_n in top_users:
            lines.append(f"  • tg=`{tg}` — `{cost:,.1f} ₽`  "
                         f"(голос: `{voice_n}` · анализ: `{analysis_n}`)")
        lines.append("")

    lines.append("*📈 Воронка (новые юзеры за 7 дней)*")
    start_count = funnel[0][1] if funnel else 0
    for label, cnt in funnel:
        pct = (cnt / start_count * 100.0) if start_count else 0.0
        # Escape *after* width-padding so the visible alignment stays right.
        safe_label = _md_escape(f"{label:<32}")
        lines.append(f"  {safe_label} `{cnt}` ({pct:.0f}%)")
    lines.append("")

    lines.append("*🎤 Whisper · 30 дней*")
    lines.append(f"  Минут: `{whisper_minutes:.1f}`  "
                 f"Средняя длительность: `{whisper_avg:.1f}` сек  "
                 f"Активных голосовых: `{voice_users}`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def cmd_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin_stats command (admin-only)."""
    tg_user_id = update.effective_user.id
    if not is_admin(tg_user_id):
        # Pretend the command does not exist for non-admins (no info leak).
        return

    db: Optional[Session] = None
    try:
        db = SessionLocal()
        text = render_admin_stats(db)
    except Exception as e:
        logger.error(f"admin_stats failed: {e}", exc_info=True)
        text = f"❌ Ошибка при сборе статистики: {e}"
    finally:
        if db is not None:
            db.close()

    # Telegram message limit is 4096 chars — our output is well under that.
    await update.effective_message.reply_text(text, parse_mode="Markdown")
