"""Main entry point for Telegram bot."""
import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, filters,
)
from telegram.request import HTTPXRequest

from bot.handlers import (
    start_command, accounts_command, report_command, help_command,
    message_handler, voice_message_handler, callback_handler,
    pre_checkout_handler, successful_payment_handler,
)
from bot.sheets import (
    sheets_command, sheets_export_command, sheets_import_command
)
from bot.menu import menu_command
from bot.admin import cmd_admin_stats
from db.session import init_db

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)


def _init_sentry() -> None:
    """Initialize Sentry if SENTRY_DSN is configured. Silent no-op otherwise."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping init.")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("ENV", "dev"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    logger.info(f"Sentry initialized (env={os.getenv('ENV', 'dev')})")


def main():
    """Main function to start the bot."""
    # Initialize observability before anything else.
    _init_sentry()

    # Initialize database
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized.")

    # Get bot token
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

    # Network settings: make outgoing requests more resilient to transient DNS/connection issues.
    request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
        connection_pool_size=20,
    )

    # Create application (disable concurrent updates to reduce parallel network calls)
    application = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .concurrent_updates(False)
        .build()
    )

    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("accounts", accounts_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("sheets", sheets_command))
    application.add_handler(CommandHandler("sheets_export", sheets_export_command))
    application.add_handler(CommandHandler("sheets_import", sheets_import_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin_stats", cmd_admin_stats))

    # Single callback handler that routes all callback prefixes
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    # Telegram Stars payment handlers
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Setup scheduled jobs (weekly/monthly digests, reminders, subscription expiry)
    from services.scheduler import setup_scheduled_jobs
    setup_scheduled_jobs(application)

    # Start bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
