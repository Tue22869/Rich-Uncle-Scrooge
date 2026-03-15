"""Main entry point for Telegram bot."""
import asyncio
import os
import logging
from decimal import Decimal

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters,
)
from telegram.request import HTTPXRequest

from bot.handlers import (
    start_command, accounts_command, report_command, help_command,
    message_handler, voice_message_handler, callback_handler
)
from bot.sheets import (
    sheets_command, sheets_export_command, sheets_import_command
)
from bot.subscription import (
    subscribe_command, plans_command, my_subscription_command,
    cancel_subscription_command, subscription_callback_handler,
    PLAN_SELECT_PREFIX, CANCEL_SUB_CONFIRM, CANCEL_SUB_ABORT,
)
from bot.admin_subscription import (
    grant_subscription_command, subscriptions_list_command,
)
from bot.middleware import check_expiring_subscriptions
from db.session import init_db, SessionLocal
from db.models import SubscriptionPlan
from payment.config import configure_yookassa

# Load environment variables
load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, log_level, logging.INFO),
)
logger = logging.getLogger(__name__)


def _seed_default_plans():
    """Create default subscription plans if none exist."""
    db = SessionLocal()
    try:
        count = db.query(SubscriptionPlan).count()
        if count > 0:
            return
        monthly = SubscriptionPlan(
            name="Premium Monthly",
            price=Decimal("299"),
            currency="RUB",
            duration_days=30,
            features_json=[
                "AI-аналитика расходов",
                "Детальные отчёты",
                "Синхронизация с Google Sheets",
                "Голосовой ввод",
            ],
            is_active=True,
        )
        yearly = SubscriptionPlan(
            name="Premium Yearly",
            price=Decimal("2490"),
            currency="RUB",
            duration_days=365,
            features_json=[
                "Всё из Monthly",
                "Скидка ~30%",
                "Приоритетная поддержка",
            ],
            is_active=True,
        )
        db.add_all([monthly, yearly])
        db.commit()
        logger.info("Default subscription plans created.")
    finally:
        db.close()


def _build_application(token: str) -> Application:
    """Build and configure the PTB Application with all handlers."""
    request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
        connection_pool_size=20,
    )

    bot_mode = os.getenv("BOT_MODE", "polling")
    builder = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .concurrent_updates(False)
    )

    # In webhook mode, we manage the updater ourselves
    if bot_mode == "webhook":
        builder = builder.updater(None)

    application = builder.build()

    # --- Core handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("accounts", accounts_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("sheets", sheets_command))
    application.add_handler(CommandHandler("sheets_export", sheets_export_command))
    application.add_handler(CommandHandler("sheets_import", sheets_import_command))
    application.add_handler(CommandHandler("help", help_command))

    # --- Subscription handlers ---
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("plans", plans_command))
    application.add_handler(CommandHandler("mysubscription", my_subscription_command))
    application.add_handler(CommandHandler("cancel_subscription", cancel_subscription_command))

    # --- Admin handlers ---
    application.add_handler(CommandHandler("grant_subscription", grant_subscription_command))
    application.add_handler(CommandHandler("subscriptions_list", subscriptions_list_command))

    # --- Callback handlers (subscription callbacks first, then general) ---
    application.add_handler(CallbackQueryHandler(
        subscription_callback_handler,
        pattern=f"^({PLAN_SELECT_PREFIX}|{CANCEL_SUB_CONFIRM}|{CANCEL_SUB_ABORT})",
    ))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # --- Message handlers ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    return application


def _run_polling(application: Application):
    """Start the bot in polling mode (for local development)."""
    # Schedule daily subscription expiration check
    application.job_queue.run_repeating(
        check_expiring_subscriptions,
        interval=86400,  # 24 hours
        first=60,  # first run after 60 seconds
    )

    logger.info("Starting bot in POLLING mode...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


async def _run_webhook(application: Application):
    """Start the bot in webhook mode with starlette (for production)."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import PlainTextResponse, JSONResponse
    from starlette.requests import Request
    import uvicorn

    from payment.webhook import yookassa_webhook_handler, set_bot_application

    webhook_url = os.getenv("WEBHOOK_URL", "")
    webhook_port = int(os.getenv("WEBHOOK_PORT", "8443"))
    webhook_secret = os.getenv("WEBHOOK_SECRET_TOKEN", "")

    if not webhook_url:
        raise ValueError("WEBHOOK_URL is required in webhook mode")

    set_bot_application(application)

    async def telegram_webhook(request: Request) -> PlainTextResponse:
        """Handle incoming Telegram updates."""
        # Verify secret token
        if webhook_secret:
            token_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if token_header != webhook_secret:
                return PlainTextResponse("Forbidden", status_code=403)

        data = await request.json()
        update = Update.de_json(data=data, bot=application.bot)
        await application.update_queue.put(update)
        return PlainTextResponse("OK")

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    routes = [
        Route("/telegram", telegram_webhook, methods=["POST"]),
        Route("/webhook/yookassa", yookassa_webhook_handler, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ]

    starlette_app = Starlette(routes=routes)

    # Schedule daily subscription expiration check
    application.job_queue.run_repeating(
        check_expiring_subscriptions,
        interval=86400,
        first=60,
    )

    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=starlette_app,
            host="0.0.0.0",
            port=webhook_port,
            log_level="info",
        )
    )

    async with application:
        await application.start()

        # Set Telegram webhook
        await application.bot.set_webhook(
            url=f"{webhook_url}/telegram",
            secret_token=webhook_secret or None,
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Telegram webhook set to %s/telegram", webhook_url)

        await webserver.serve()
        await application.stop()


def main():
    """Main function to start the bot."""
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized.")

    # Seed default subscription plans
    _seed_default_plans()

    # Configure payment system
    configure_yookassa()

    # Get bot token
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

    application = _build_application(token)

    bot_mode = os.getenv("BOT_MODE", "polling")
    if bot_mode == "webhook":
        asyncio.run(_run_webhook(application))
    else:
        _run_polling(application)


if __name__ == "__main__":
    main()
