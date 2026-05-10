"""Tests for services/webhook_server.py."""
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from services.webhook_server import build_app


@pytest.fixture
async def client():
    app = build_app()
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.mark.asyncio
async def test_healthcheck_ok(client):
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.text()) == "ok"


@pytest.mark.asyncio
async def test_webhook_acks_unknown_event(client):
    with patch("services.webhook_server.confirm_payment") as mock_confirm:
        resp = await client.post(
            "/yookassa/webhook",
            json={"event": "payment.canceled", "object": {"id": "abc"}},
        )
    assert resp.status == 200
    mock_confirm.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_acks_bad_json(client):
    resp = await client.post(
        "/yookassa/webhook",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_webhook_calls_confirm_on_succeeded(client):
    with patch("services.webhook_server.confirm_payment", return_value=None) as mock_confirm:
        resp = await client.post(
            "/yookassa/webhook",
            json={"event": "payment.succeeded", "object": {"id": "pay_123"}},
        )
    assert resp.status == 200
    mock_confirm.assert_called_once()
    args, _kwargs = mock_confirm.call_args
    # confirm_payment(db, payment_id)
    assert args[1] == "pay_123"


@pytest.mark.asyncio
async def test_webhook_handles_confirm_exception(client):
    with patch("services.webhook_server.confirm_payment", side_effect=RuntimeError("boom")):
        resp = await client.post(
            "/yookassa/webhook",
            json={"event": "payment.succeeded", "object": {"id": "pay_err"}},
        )
    # Still 200 — we don't want YooKassa to retry forever on our internal errors.
    assert resp.status == 200


@pytest.mark.asyncio
async def test_webhook_missing_payment_id(client):
    with patch("services.webhook_server.confirm_payment") as mock_confirm:
        resp = await client.post(
            "/yookassa/webhook",
            json={"event": "payment.succeeded", "object": {}},
        )
    assert resp.status == 200
    mock_confirm.assert_not_called()
