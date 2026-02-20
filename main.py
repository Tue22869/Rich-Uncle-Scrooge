"""Main entry point for Telegram bot."""
import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest

from bot.handlers import (
    start_command, accounts_command, report_command, help_command,
    sheets_command, sheets_export_command, sheets_import_command,
    message_handler, voice_message_handler, callback_handler
)
from db.session import init_db

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    """Main function to start the bot."""
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
    
    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("accounts", accounts_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("sheets", sheets_command))
    application.add_handler(CommandHandler("sheets_export", sheets_export_command))
    application.add_handler(CommandHandler("sheets_import", sheets_import_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))
    
    # Start bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

