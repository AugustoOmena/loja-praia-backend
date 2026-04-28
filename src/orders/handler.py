import json
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import APIGatewayHttpResolver
from aws_lambda_powertools.utilities.parser import parse
from aws_lambda_powertools.utilities.typing import LambdaContext

from command_handler import GetOrderDetailCommand, ListOrdersCommand, RequestCancelOrRefundCommand, UpdateOrderCommand
from shared.responses import http_response
from shared.supabase_utils import get_authorization_header
from schemas import BackofficeCancelInput, CancelRequestInput, OrderStatusUpdate
from service import OrderService

logger = Logger(service="orders")
app = APIGatewayHttpResolver()


def _query_params() -> dict:
    return app.current_event.raw_event.get("queryStringParameters") or {}


def _headers() -> dict:
    return app.current_event.raw_event.get("headers") or {}


@app.get("/pedidos")
def list_orders() -> dict:
    service = OrderService()
    command = ListOrdersCommand.from_event(
        query_params=_query_params(),
        headers=_headers(),
        authorization_header=get_authorization_header(app.current_event.raw_event),
    )
    if command.is_backoffice and command.user_id:
        try:
            result = service.list_all_orders_for_admin(
                command.user_id,
                page=command.page,
                limit=command.limit,
                authorization_header=command.authorization_header,
            )
        except PermissionError as e:
            return http_response(403, {"error": str(e)})
        return http_response(200, result)
    if not command.user_id:
        return http_response(400, {"error": "user_id obrigatório para listar pedidos"})
    result = service.list_orders_by_customer(command.user_id, page=command.page, limit=command.limit)
    return http_response(200, result)


@app.get("/pedidos/<order_id>")
def get_order_detail(order_id: str) -> dict:
    service = OrderService()
    command = GetOrderDetailCommand.from_event(
        order_id=order_id,
        query_params=_query_params(),
        body=_body_json(app.current_event.raw_event),
        headers=_headers(),
        authorization_header=get_authorization_header(app.current_event.raw_event),
    )
    if command.is_backoffice:
        try:
            result = service.get_order_detail_for_admin(
                command.order_id,
                command.user_id,
                authorization_header=command.authorization_header,
            )
        except PermissionError as e:
            return http_response(403, {"error": str(e)})
        return http_response(200, result)
    result = service.get_order_detail(command.order_id, command.user_id)
    return http_response(200, result)


@app.post("/pedidos/<order_id>/solicitar-cancelamento")
def request_cancel_or_refund(order_id: str) -> dict:
    service = OrderService()
    body = _body_json(app.current_event.raw_event)
    command = RequestCancelOrRefundCommand.from_event(order_id=order_id, query_params=_query_params(), body=body)
    payload = parse(event=_body_json(app.current_event.raw_event), model=CancelRequestInput)
    result = service.request_cancel_or_refund(command.order_id, command.user_id, payload)
    return http_response(201, result)


@app.put("/pedidos/<order_id>")
def update_order(order_id: str) -> dict:
    service = OrderService()
    command = UpdateOrderCommand.from_event(order_id=order_id, headers=_headers(), body=_body_json(app.current_event.raw_event))
    if not command.is_backoffice:
        return http_response(403, {"error": "Acesso restrito ao backoffice"})
    body = command.normalized_body
    if "delivery_status" in body and "refund_method" not in body:
        payload = parse(event=body, model=OrderStatusUpdate)
        result = service.update_order_delivery_status(command.order_id, payload.delivery_status)
        return http_response(200, result)
    if "refund_method" in body and body.get("refund_method") is not None:
        payload = parse(event=body, model=BackofficeCancelInput)
        result = service.backoffice_cancel_and_refund(command.order_id, payload)
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
