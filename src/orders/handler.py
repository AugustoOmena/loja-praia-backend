"""
Handler for orders microservice.

Routes:
- GET /pedidos/{order_id}?user_id=...  Customer: full order detail (user_id = dono do pedido). Backoffice: X-Backoffice: true + user_id = admin + Authorization (mesmo que na listagem).
- GET /pedidos?user_id=...&page=&limit=  Customer: simplified list; Backoffice (X-Backoffice: true): list all if user role admin
- POST /pedidos/{order_id}/solicitar-cancelamento  Customer: cancel/refund request (7 days)
- PUT /pedidos/{order_id}  Backoffice: editar status de entrega (body {"delivery_status": "shipped"}) ou cancel/reembolso (header X-Backoffice: true)
"""

import json
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import APIGatewayHttpResolver
from aws_lambda_powertools.utilities.parser import parse
from aws_lambda_powertools.utilities.typing import LambdaContext

from shared.responses import http_response
from shared.supabase_utils import get_authorization_header
from schemas import BackofficeCancelInput, CancelRequestInput, OrderStatusUpdate
from service import OrderService

logger = Logger(service="orders")
app = APIGatewayHttpResolver()


def _is_backoffice(headers: dict) -> bool:
    return headers.get("x-backoffice", "").lower() == "true" or headers.get("X-Backoffice", "").lower() == "true"


def _query_params() -> dict:
    return app.current_event.raw_event.get("queryStringParameters") or {}


def _headers() -> dict:
    return app.current_event.raw_event.get("headers") or {}


def _user_id(body_fallback: bool = True) -> str | None:
    query_params = _query_params()
    if query_params.get("user_id"):
        return query_params.get("user_id")
    if not body_fallback:
        return None
    event = app.current_event.raw_event
    body = _body_json(event)
    return body.get("user_id")


@app.get("/pedidos")
def list_orders() -> dict:
    service = OrderService()
    query_params = _query_params()
    user_id = _user_id(body_fallback=False)
    page = int(query_params.get("page", 1))
    limit = int(query_params.get("limit", 20))
    headers = _headers()
    if _is_backoffice(headers) and user_id:
        try:
            result = service.list_all_orders_for_admin(
                user_id,
                page=page,
                limit=limit,
                authorization_header=get_authorization_header(app.current_event.raw_event),
            )
        except PermissionError as e:
            return http_response(403, {"error": str(e)})
        return http_response(200, result)
    if not user_id:
        return http_response(400, {"error": "user_id obrigatório para listar pedidos"})
    result = service.list_orders_by_customer(user_id, page=page, limit=limit)
    return http_response(200, result)


@app.get("/pedidos/<order_id>")
def get_order_detail(order_id: str) -> dict:
    service = OrderService()
    user_id = _user_id(body_fallback=True)
    if not user_id:
        return http_response(400, {"error": "user_id obrigatório para ver detalhe do pedido"})
    headers = _headers()
    if _is_backoffice(headers):
        try:
            result = service.get_order_detail_for_admin(
                order_id,
                user_id,
                authorization_header=get_authorization_header(app.current_event.raw_event),
            )
        except PermissionError as e:
            return http_response(403, {"error": str(e)})
        return http_response(200, result)
    result = service.get_order_detail(order_id, user_id)
    return http_response(200, result)


@app.post("/pedidos/<order_id>/solicitar-cancelamento")
def request_cancel_or_refund(order_id: str) -> dict:
    service = OrderService()
    user_id = _user_id(body_fallback=True)
    if not user_id:
        return http_response(400, {"error": "user_id obrigatório"})
    payload = parse(event=_body_json(app.current_event.raw_event), model=CancelRequestInput)
    result = service.request_cancel_or_refund(order_id, user_id, payload)
    return http_response(201, result)


@app.put("/pedidos/<order_id>")
def update_order(order_id: str) -> dict:
    service = OrderService()
    if not _is_backoffice(_headers()):
        return http_response(403, {"error": "Acesso restrito ao backoffice"})

    body = _body_json(app.current_event.raw_event)
    if "delivery_status" not in body and "status" in body:
        body["delivery_status"] = body["status"]
    if "delivery_status" in body and "refund_method" not in body:
        payload = parse(event=body, model=OrderStatusUpdate)
        result = service.update_order_delivery_status(order_id, payload.delivery_status)
        return http_response(200, result)
    if "refund_method" in body and body.get("refund_method") is not None:
        payload = parse(event=body, model=BackofficeCancelInput)
        result = service.backoffice_cancel_and_refund(order_id, payload)
        return http_response(200, result)

    return http_response(
        400,
        {
            "error": "Envie {\"delivery_status\": \"...\"} para editar entrega ou payload de cancelamento/reembolso (refund_method, etc.)"
        },
    )


def _normalize_event_for_router(event: dict) -> dict:
    normalized = dict(event)
    path_params = normalized.get("pathParameters") or {}
    proxy = path_params.get("proxy") or ""

    if not normalized.get("rawPath"):
        if proxy:
            normalized["rawPath"] = f"/pedidos/{proxy}"
        else:
            normalized["rawPath"] = "/pedidos"

    request_context = normalized.get("requestContext") or {}
    http_data = request_context.get("http") or {}
    if not request_context.get("stage"):
        request_context["stage"] = "$default"
    if not http_data.get("path"):
        http_data["path"] = normalized["rawPath"]
    request_context["http"] = http_data
    normalized["requestContext"] = request_context
    return normalized


def _unwrap_http_response_from_router(response: dict) -> dict:
    body = response.get("body")
    if not isinstance(body, str):
        return response
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return response
    if isinstance(decoded, dict) and {"statusCode", "headers", "body"}.issubset(decoded.keys()):
        return decoded
    return response


@logger.inject_lambda_context
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method")
    if method == "OPTIONS":
        return http_response(200, {})
    try:
        response = app.resolve(_normalize_event_for_router(event), context)
        return _unwrap_http_response_from_router(response)
    except ValueError as e:
        logger.warning(f"Validação: {e!s}")
        return http_response(400, {"error": "Dados inválidos", "details": str(e)})
    except Exception as e:
        logger.exception("Erro no processamento")
        return http_response(500, {"error": str(e)})


def _body_json(event: dict) -> dict:
    body = event.get("body")
    if body is None:
        return {}
    if isinstance(body, str):
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {}
    return body if isinstance(body, dict) else {}
