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
# resolve() — uses event as-is and unwraps the router envelope
# ---------------------------------------------------------------------------

def resolve(event: dict, context: Any) -> dict:
    _validate_event_contract(event)
    return _unwrap(app.resolve(event, context))


def _validate_event_contract(event: dict) -> None:
    request_context = event.get("requestContext") or {}
    http = request_context.get("http") or {}
    if not event.get("rawPath"):
        raise ValueError("Evento inválido: rawPath obrigatório")
    if not request_context.get("stage"):
        raise ValueError("Evento inválido: requestContext.stage obrigatório")
    if not http.get("method"):
        raise ValueError("Evento inválido: requestContext.http.method obrigatório")


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
