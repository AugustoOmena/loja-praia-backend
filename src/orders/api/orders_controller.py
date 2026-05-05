from aws_lambda_powertools.utilities.parser import parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from domain.command_handlers.orders_command_handler import (
    GetOrderDetailAdminCommand,
    GetOrderDetailCommand,
    ListAllOrdersAdminCommand,
    ListOrdersCommand,
    RequestCancelOrRefundCommand,
    UpdateDeliveryStatusCommand,
    build_update_order_command,
)
from router import app, body_json, current_event, logger, query_params, resolve, route
from domain.validators.orders_validator import BackofficeCancelInput, CancelRequestInput
from domain.services.orders_service import OrderService
from shared.responses import http_response
from shared.supabase_utils import get_authorization_header


@app.get("/pedidos")
@route
def list_orders() -> dict:
    command = ListOrdersCommand.from_event(query_params=query_params())
    result = OrderService().list_orders_by_customer(command.user_id, page=command.page, limit=command.limit)
    return http_response(200, result)


@app.get("/pedidos/<order_id>")
@route
def get_order_detail(order_id: str) -> dict:
    command = GetOrderDetailCommand.from_event(order_id=order_id, query_params=query_params(), body=body_json())
    result = OrderService().get_order_detail(command.order_id, command.user_id)
    return http_response(200, result)


@app.post("/pedidos/<order_id>/solicitar-cancelamento")
@route
def request_cancel_or_refund(order_id: str) -> dict:
    body = body_json()
    command = RequestCancelOrRefundCommand.from_event(order_id=order_id, query_params=query_params(), body=body)
    payload = parse(event=body, model=CancelRequestInput)
    result = OrderService().request_cancel_or_refund(command.order_id, command.user_id, payload)
    return http_response(201, result)


@app.get("/backoffice/pedidos")
@route
def list_orders_admin() -> dict:
    command = ListAllOrdersAdminCommand.from_event(
        query_params=query_params(),
        authorization_header=get_authorization_header(current_event()),
    )
    result = OrderService().list_all_orders_for_admin(
        command.user_id,
        page=command.page,
        limit=command.limit,
        authorization_header=command.authorization_header,
    )
    return http_response(200, result)


@app.get("/backoffice/pedidos/<order_id>")
@route
def get_order_detail_admin(order_id: str) -> dict:
    command = GetOrderDetailAdminCommand.from_event(
        order_id=order_id,
        query_params=query_params(),
        body=body_json(),
        authorization_header=get_authorization_header(current_event()),
    )
    result = OrderService().get_order_detail_for_admin(
        command.order_id,
        command.user_id,
        authorization_header=command.authorization_header,
    )
    return http_response(200, result)


@app.put("/backoffice/pedidos/<order_id>")
@route
def update_order(order_id: str) -> dict:
    command = build_update_order_command(order_id=order_id, body=body_json())
    if isinstance(command, UpdateDeliveryStatusCommand):
        result = OrderService().update_order_delivery_status(command.order_id, command.delivery_status)
    else:
        payload = parse(event=command.raw_body, model=BackofficeCancelInput)
        result = OrderService().backoffice_cancel_and_refund(command.order_id, payload)
    return http_response(200, result)


# ---------------------------------------------------------------------------
# Lambda entrypoint
# ---------------------------------------------------------------------------

@logger.inject_lambda_context
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return http_response(200, {})
    return resolve(event, context)
