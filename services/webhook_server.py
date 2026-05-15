"""HTTP webhook receiver for YooKassa payment notifications.

Run as a separate process alongside the bot:

    python -m services.webhook_server

Listens on $WEBHOOK_HOST:$WEBHOOK_PORT (default 0.0.0.0:8080) and processes
POST /yookassa/webhook events. We always respond 200 so YooKassa stops retrying
(failures are logged + sent to Sentry instead).

YooKassa identifies senders by source IP range (185.71.76.0/27,
185.71.77.0/27, 77.75.153.0/25, 77.75.156.11, 77.75.156.35, 2a02:5180::/32).
Configure your reverse proxy / firewall to only accept those.
"""
import logging
import os

from aiohttp import web
from dotenv import load_dotenv

from db.session import SessionLocal
from services.billing import confirm_payment

load_dotenv()

logger = logging.getLogger(__name__)

# Events we actually act on; everything else is acked but ignored.
_HANDLED_EVENTS = {"payment.succeeded"}


async def yookassa_webhook(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"YooKassa webhook: bad JSON: {e}")
        return web.Response(status=200, text="ok")  # 200 stops retries

    event = body.get("event")
    obj = body.get("object", {})
    payment_id = obj.get("id")
    logger.info(f"YooKassa webhook event={event} payment_id={payment_id}")

    if event not in _HANDLED_EVENTS:
        return web.Response(status=200, text="ignored")

    if not payment_id:
        logger.error("YooKassa webhook: payment.succeeded without object.id")
        return web.Response(status=200, text="ok")

    db = SessionLocal()
    try:
        # confirm_payment re-fetches the payment via the YooKassa API and double-checks
        # status, so we don't have to trust the webhook body alone.
        sub = confirm_payment(db, payment_id)
        if sub is None:
            logger.warning(f"YooKassa webhook: confirm_payment returned None for {payment_id}")
        else:
            logger.info(f"YooKassa webhook: subscription {sub.id} confirmed via webhook")
    except Exception as e:
        logger.error(f"YooKassa webhook: confirm_payment crashed: {e}", exc_info=True)
    finally:
        db.close()

    return web.Response(status=200, text="ok")


async def healthcheck(_request: web.Request) -> web.Response:
    return web.Response(status=200, text="ok")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/yookassa/webhook", yookassa_webhook)
    app.router.add_get("/health", healthcheck)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

    # Optional Sentry hook — same DSN as the bot.
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_sdk.init(
                dsn=dsn,
                environment=os.getenv("ENV", "dev"),
                integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            )
            logger.info("Sentry initialized for webhook server")
        except ImportError:
            logger.warning("SENTRY_DSN set but sentry-sdk missing; skipping")

    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "8080"))
    logger.info(f"Starting YooKassa webhook server on {host}:{port}")
    web.run_app(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
