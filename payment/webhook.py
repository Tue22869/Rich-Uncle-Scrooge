"""YooKassa webhook HTTP handler for Starlette."""
import ipaddress
import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from payment.config import YOOKASSA_IPS
from payment.yookassa_service import process_webhook

logger = logging.getLogger(__name__)

# Pre-parse IP networks for fast lookup
_allowed_networks = []
for cidr in YOOKASSA_IPS:
    try:
        _allowed_networks.append(ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        pass


def _is_yookassa_ip(ip_str: str) -> bool:
    """Check if the request IP belongs to YooKassa."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _allowed_networks)
    except ValueError:
        return False


# Reference to the Telegram bot application, set from main.py
_bot_app = None


def set_bot_application(app):
    """Store reference to the PTB Application for sending notifications."""
    global _bot_app
    _bot_app = app


async def yookassa_webhook_handler(request: Request) -> JSONResponse:
    """Handle YooKassa webhook POST requests."""
    # IP verification (skip in test mode)
    skip_ip_check = os.getenv("YOOKASSA_SKIP_IP_CHECK", "false").lower() == "true"
    if not skip_ip_check:
        client_ip = request.client.host if request.client else ""
        forwarded = request.headers.get("x-forwarded-for", "")
        real_ip = forwarded.split(",")[0].strip() if forwarded else client_ip

        if not _is_yookassa_ip(real_ip):
            logger.warning("Webhook from untrusted IP: %s", real_ip)
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        logger.error("Invalid JSON in webhook body")
        return JSONResponse({"error": "Bad request"}, status_code=400)

    tg_user_id, event = process_webhook(body)

    # Send Telegram notification if payment was processed
    if tg_user_id and _bot_app:
        try:
            from yookassa.domain.notification import WebhookNotificationEventType
            if event == WebhookNotificationEventType.PAYMENT_SUCCEEDED:
                await _bot_app.bot.send_message(
                    chat_id=tg_user_id,
                    text="✅ Оплата прошла успешно! Премиум-подписка активирована.\n"
                         "Используйте /mysubscription чтобы проверить статус.",
                )
            elif event == WebhookNotificationEventType.PAYMENT_CANCELED:
                await _bot_app.bot.send_message(
                    chat_id=tg_user_id,
                    text="❌ Оплата отменена. Попробуйте ещё раз: /subscribe",
                )
        except Exception as e:
            logger.error("Failed to send payment notification: %s", e)

    # Always return 200 so YooKassa doesn't retry
    return JSONResponse({"status": "ok"})
