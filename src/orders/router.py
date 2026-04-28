"""
Router infrastructure for the orders microservice.

Responsibilities:
- Powertools resolver instance (app)
- Context accessors: current_event, query_params, headers, body_json
- @route decorator: maps exceptions to HTTP responses
- resolve(): normalizes incoming event and unwraps the router response
"""
from __future__ import annotations

import json
from functools import wraps
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import APIGatewayHttpResolver

from exceptions import ForbiddenError
from shared.responses import http_response

logger = Logger(service="orders")
app = APIGatewayHttpResolver()


# ---------------------------------------------------------------------------
# Context accessors — read from the active request inside a route handler
# ---------------------------------------------------------------------------

def current_event() -> dict:
    return app.current_event.raw_event


def query_params() -> dict:
    return current_event().get("queryStringParameters") or {}


def headers() -> dict:
    return current_event().get("headers") or {}


def body_json() -> dict:
    raw = current_event().get("body")
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# @route decorator — centralizes exception → HTTP response mapping
# ---------------------------------------------------------------------------

def route(fn: Any) -> Any:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except (ForbiddenError, PermissionError) as e:
            return http_response(403, {"error": str(e)})
        except ValueError as e:
            logger.warning(f"Validação: {e!s}")
            return http_response(400, {"error": "Dados inválidos", "details": str(e)})
        except Exception as e:
            logger.exception("Erro no processamento")
            return http_response(500, {"error": str(e)})
    return wrapper


# ---------------------------------------------------------------------------
# resolve() — normalizes event format and unwraps the router envelope
# ---------------------------------------------------------------------------

def resolve(event: dict, context: Any) -> dict:
    return _unwrap(app.resolve(_normalize(event), context))


def _normalize(event: dict) -> dict:
    """Fills in rawPath and requestContext fields that API Gateway provides
    in production but may be absent in local/test events."""
    normalized = dict(event)
    if not normalized.get("rawPath"):
        proxy = (normalized.get("pathParameters") or {}).get("proxy") or ""
        hdrs = (normalized.get("headers") or {})
        is_backoffice = (
            hdrs.get("x-backoffice", "").lower() == "true"
            or hdrs.get("X-Backoffice", "").lower() == "true"
        )
        prefix = "/backoffice/pedidos" if is_backoffice else "/pedidos"
        normalized["rawPath"] = f"{prefix}/{proxy}" if proxy else prefix

    request_context = normalized.get("requestContext") or {}
    http_data = request_context.get("http") or {}
    if not request_context.get("stage"):
        request_context["stage"] = "$default"
    if not http_data.get("path"):
        http_data["path"] = normalized["rawPath"]
    request_context["http"] = http_data
    normalized["requestContext"] = request_context
    return normalized


def _unwrap(response: dict) -> dict:
    """The Powertools resolver wraps our http_response() dict as a JSON string
    inside its own envelope. This extracts the original response back out."""
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
