"""Scheduler service: weekly/monthly digests, reminders, subscription expiry."""
import logging
from datetime import datetime, time

from telegram.ext import Application

from db.session import SessionLocal
from db.models import User
from bot.middleware import _has_premium_access
from services.billing import expire_subscriptions
from services.retention import (
    generate_weekly_digest_llm, generate_monthly_digest_llm,
    get_users_needing_reminder, get_tip_of_the_day,
)

logger = logging.getLogger(__name__)


def setup_scheduled_jobs(application: Application):
    """Register all scheduled jobs with the bot's JobQueue."""
    job_queue = application.job_queue
    if not job_queue:
        logger.warning("JobQueue not available, scheduled jobs disabled")
        return

    # Weekly digest: Sunday 20:00 UTC
    job_queue.run_daily(
        _send_weekly_digests,
        time=time(hour=20, minute=0),
        days=(6,),  # Sunday
        name="weekly_digest",
    )

    # Monthly digest: 1st of each month at 10:00 UTC
    job_queue.run_daily(
        _send_monthly_digests,
        time=time(hour=10, minute=0),
        name="monthly_digest",
    )

    # Inactivity reminders: daily at 18:00 UTC
    job_queue.run_daily(
        _send_inactivity_reminders,
        time=time(hour=18, minute=0),
        name="inactivity_reminder",
    )

    # Expire subscriptions: every 6 hours
    job_queue.run_repeating(
        _expire_subscriptions_job,
        interval=6 * 60 * 60,
        first=60,
        name="expire_subscriptions",
    )

    logger.info("Scheduled jobs registered: weekly_digest, monthly_digest, inactivity_reminder, expire_subscriptions")


async def _send_weekly_digests(context):
    """Send weekly digest to all premium users."""
    db = SessionLocal()
    try:
        users = db.query(User).all()
        sent = 0
        for user in users:
            if not _has_premium_access(db, user):
                continue
            try:
                digest = await generate_weekly_digest_llm(db, user)
                if digest:
                    await context.bot.send_message(
                        chat_id=user.tg_user_id,
                        text=digest,
                        parse_mode="Markdown",
                    )
                    sent += 1
            except Exception as e:
                logger.error(f"Failed to send weekly digest to user {user.tg_user_id}: {e}")
        logger.info(f"Weekly digest sent to {sent} users")
    finally:
        db.close()


async def _send_monthly_digests(context):
    """Send monthly digest on the 1st of each month."""
    if datetime.utcnow().day != 1:
        return

    db = SessionLocal()
    try:
        users = db.query(User).all()
        sent = 0
        for user in users:
            if not _has_premium_access(db, user):
                continue
            try:
                digest = await generate_monthly_digest_llm(db, user)
                if digest:
                    await context.bot.send_message(
                        chat_id=user.tg_user_id,
                        text=digest,
                        parse_mode="Markdown",
                    )
                    sent += 1
            except Exception as e:
                logger.error(f"Failed to send monthly digest to user {user.tg_user_id}: {e}")
        logger.info(f"Monthly digest sent to {sent} users")
    finally:
        db.close()


async def _send_inactivity_reminders(context):
    """Send gentle reminder to users inactive for 3+ days."""
    db = SessionLocal()
    try:
        users = get_users_needing_reminder(db, days_inactive=3)
        sent = 0
        for user in users:
            try:
                tip = get_tip_of_the_day()
                text = (
                    "👋 Давно не видел записей!\n\n"
                    "Не забывай записывать расходы — это помогает держать финансы под контролем.\n\n"
                    f"{tip}"
                )
                await context.bot.send_message(
                    chat_id=user.tg_user_id,
                    text=text,
                )
                # Update activity date so we don't spam
                user.last_activity_date = datetime.utcnow().strftime("%Y-%m-%d")
                db.commit()
                sent += 1
            except Exception as e:
                logger.error(f"Failed to send reminder to user {user.tg_user_id}: {e}")
        if sent > 0:
            logger.info(f"Inactivity reminders sent to {sent} users")
    finally:
        db.close()


async def _expire_subscriptions_job(context):
    """Periodic job to mark expired subscriptions."""
    db = SessionLocal()
    try:
        count = expire_subscriptions(db)
        if count > 0:
            logger.info(f"Expired {count} subscriptions")
    finally:
        db.close()
