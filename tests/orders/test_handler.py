"""Unit tests for orders Lambda handler."""

import json
from unittest.mock import patch, MagicMock

import pytest

from src.orders.handler import lambda_handler


@pytest.fixture
def mock_order_service():
    """Mock OrderService to avoid real DB and business logic."""
    with patch("src.orders.handler.OrderService") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


def _get_event(
    method: str = "GET",
    raw_path: str = "/backoffice/pedidos",
    path_proxy: str = "",
    user_id: str | None = "admin-user-123",
) -> dict:
    """Build API Gateway-like event for orders handler."""
    return {
        "rawPath": raw_path,
        "requestContext": {"stage": "$default", "http": {"method": method, "path": raw_path}},
        "pathParameters": {"proxy": path_proxy} if path_proxy else {},
        "queryStringParameters": {"user_id": user_id} if user_id else None,
        "headers": {},
        "body": None,
    }


def test_get_orders_admin_returns_user_email(mock_order_service: MagicMock) -> None:
    """GET /backoffice/pedidos with user_id returns admin list."""
    mock_order_service.list_all_orders_for_admin.return_value = {
        "data": [
            {
                "id": "ord-1",
                "user_id": "user-a",
                "user_email": "comprador@example.com",
                "payment_status": "approved",
                "delivery_status": "pending",
                "total_amount": 99.90,
                "created_at": "2025-02-01T12:00:00Z",
                "payment_method": "pix",
            },
            {
                "id": "ord-2",
                "user_id": "user-b",
                "user_email": "outro@example.com",
                "payment_status": "pending",
                "delivery_status": "pending",
                "total_amount": 50.00,
                "created_at": "2025-02-02T10:00:00Z",
                "payment_method": "credit_card",
            },
        ],
        "count": 2,
    }

    event = _get_event(raw_path="/backoffice/pedidos")
    response = lambda_handler(event, MagicMock())

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "data" in body
    assert len(body["data"]) == 2
    assert body["data"][0]["user_email"] == "comprador@example.com"
    assert body["data"][1]["user_email"] == "outro@example.com"
    assert body["count"] == 2
    mock_order_service.list_all_orders_for_admin.assert_called_once()


def test_get_order_detail_backoffice_calls_admin_method(mock_order_service: MagicMock) -> None:
    """GET /backoffice/pedidos/{id} usa fluxo admin."""
    mock_order_service.get_order_detail_for_admin.return_value = {
        "id": "75a4a6e0-b3a9-4a1e-a908-169835bbd574",
        "user_id": "customer-uuid",
        "payment_status": "approved",
        "delivery_status": "pending",
        "items": [],
    }
    event = {
        "rawPath": "/backoffice/pedidos/75a4a6e0-b3a9-4a1e-a908-169835bbd574",
        "requestContext": {"stage": "$default", "http": {"method": "GET", "path": "/backoffice/pedidos/75a4a6e0-b3a9-4a1e-a908-169835bbd574"}},
        "pathParameters": {"proxy": "75a4a6e0-b3a9-4a1e-a908-169835bbd574"},
        "queryStringParameters": {"user_id": "531d3a84-9a7b-450b-a307-0b93d5eed907"},
        "headers": {"Authorization": "Bearer token"},
        "body": None,
    }
    response = lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["id"] == "75a4a6e0-b3a9-4a1e-a908-169835bbd574"
    mock_order_service.get_order_detail_for_admin.assert_called_once()
    mock_order_service.get_order_detail.assert_not_called()


def test_get_order_detail_customer_calls_customer_method(mock_order_service: MagicMock) -> None:
    """GET /pedidos/{id} sem backoffice: user_id deve ser o dono do pedido."""
    mock_order_service.get_order_detail.return_value = {"id": "ord-1", "items": []}
    event = {
        "rawPath": "/pedidos/ord-1",
        "requestContext": {"stage": "$default", "http": {"method": "GET", "path": "/pedidos/ord-1"}},
        "pathParameters": {"proxy": "ord-1"},
        "queryStringParameters": {"user_id": "customer-uuid"},
        "headers": {},
        "body": None,
    }
    response = lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200
    mock_order_service.get_order_detail.assert_called_once_with("ord-1", "customer-uuid")
    mock_order_service.get_order_detail_for_admin.assert_not_called()
