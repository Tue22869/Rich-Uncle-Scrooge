"""YooKassa payment configuration."""
import os
import logging

from yookassa import Configuration

logger = logging.getLogger(__name__)

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PAYMENT_RETURN_URL = WEBHOOK_URL + "/payment/success" if WEBHOOK_URL else ""

# YooKassa IP whitelist for webhook verification
# https://yookassa.ru/developers/using-api/webhooks#ip
YOOKASSA_IPS = {
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.156.35",
    "77.75.154.128/25",
    "2a02:5180::/32",
}


def configure_yookassa() -> bool:
    """Configure YooKassa SDK. Returns True if configured successfully."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.warning("YooKassa credentials not configured. Payment features disabled.")
        return False

    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
    logger.info("YooKassa configured for shop %s", YOOKASSA_SHOP_ID)
    return True
