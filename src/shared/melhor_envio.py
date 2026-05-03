"""
Melhor Envio API client: quote, cart, shipment and tracking operations.

Expects env: MELHOR_ENVIO_TOKEN, CEP_ORIGEM; optional MELHOR_ENVIO_API_URL.
"""

import json
import math
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API_BASE = "https://sandbox.melhorenvio.com.br"
CALCULATE_PATH = "/api/v2/me/shipment/calculate"
CART_PATH = "/api/v2/me/cart"
CHECKOUT_PATH = "/api/v2/me/shipment/checkout"
GENERATE_PATH = "/api/v2/me/shipment/generate"
TRACKING_PATH = "/api/v2/me/shipment/tracking"
REQUEST_TIMEOUT_SEC = 15


class MelhorEnvioAPIError(Exception):
    """Raised when the external API returns an error or is unreachable."""

    pass


def _env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key) or default
    if not value and key in ("MELHOR_ENVIO_TOKEN", "CEP_ORIGEM"):
        raise MelhorEnvioAPIError(f"Variável de ambiente obrigatória não definida: {key}")
    return value or ""


def _parse_quote_option(entry: dict[str, Any]) -> dict[str, Any] | None:
    # Carrier name vem de company.name ("Jadlog", "Correios"); entry.name é o serviço (".Package", "PAC")
    company = entry.get("company") or {}
    carrier_name = company.get("name") or entry.get("company_name") or entry.get("name") or "Transportadora"
    service_name = entry.get("name") or ""

    value = entry.get("price")
    if value is None:
        return None
    try:
        price_float = float(value)
    except (TypeError, ValueError):
        return None

    delivery = (
        entry.get("custom_delivery_time")
        or entry.get("delivery_time")
        or entry.get("delivery_time_min")
    )
    try:
        days = int(delivery) if delivery is not None else None
    except (TypeError, ValueError):
        days = None

    # entry["id"] é o service_id numérico do ME (1=PAC, 2=SEDEX, 3=Jadlog .Package…)
    raw_id = entry.get("id")
    try:
        service_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        service_id = None

    # agency_id: presente na resposta do calculate para transportadoras que exigem agência (Jadlog)
    agency = entry.get("agency") or {}
    raw_agency_id = agency.get("id")
    try:
        agency_id = int(raw_agency_id) if raw_agency_id is not None else None
    except (TypeError, ValueError):
        agency_id = None

    return {
        "transportadora": carrier_name,
        "servico": service_name,
        "preco": round(price_float, 2),
        "prazo_entrega_dias": days,
        "service": service_id,
        "agency_id": agency_id,
    }


def _parse_response(body: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if isinstance(body, list):
        for item in body:
            parsed = _parse_quote_option(item if isinstance(item, dict) else {})
            if parsed:
                options.append(parsed)
        return options
    if not isinstance(body, dict):
        return options
    for key in ("id", "packages", "data"):
        if key not in body:
            continue
        val = body[key]
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    inner = item.get("options") or item.get("services") or [item]
                    if not isinstance(inner, list):
                        inner = [inner]
                    for opt in inner:
                        parsed = _parse_quote_option(opt if isinstance(opt, dict) else {})
                        if parsed:
                            options.append(parsed)
            return options
        if isinstance(val, dict):
            parsed = _parse_quote_option(val)
            if parsed:
                options.append(parsed)
            return options
    parsed = _parse_quote_option(body)
    if parsed:
        options.append(parsed)
    return options


def get_quote(
    cep_destino: str,
    products: list[dict[str, Any]],
    *,
    timeout_sec: float | None = None,
) -> list[dict[str, Any]]:
    """
    Call Melhor Envio calculate API.

    Args:
        cep_destino: Destination postal code (8 digits).
        products: List of dicts with width, height, length (cm), weight (kg),
                  quantity, and optional insurance_value (default 1). Optional "id" per product.
        timeout_sec: Timeout HTTP (segundos). Padrão ``REQUEST_TIMEOUT_SEC``. Pagamento deve usar valor
            menor para caber no teto ~30s do API Gateway HTTP + MP + Supabase + Firebase.

    Returns:
        List of dicts with keys: transportadora, preco, prazo_entrega_dias, service (id do serviço Melhor Envio).

    Raises:
        MelhorEnvioAPIError: On missing env, connection/timeout or API error.
    """
    http_timeout = float(timeout_sec) if timeout_sec is not None else float(REQUEST_TIMEOUT_SEC)
    base = (os.environ.get("MELHOR_ENVIO_API_URL") or DEFAULT_API_BASE).rstrip("/")
    url = f"{base}{CALCULATE_PATH}"
    token = _env("MELHOR_ENVIO_TOKEN", "")
    cep_origem = _env("CEP_ORIGEM", "")

    def _dim_int(val) -> int:
        """Dimensões em cm como int (já ceil no shipping); evita divergência com Carrinho."""
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        return int(math.ceil(float(val)))

    def _weight_3(val) -> float:
        """Peso em kg com no máximo 3 casas decimais (Melhor Envio)."""
        return round(float(val), 3)

    def _money_2(val) -> float:
        return round(float(val), 2)

    payload_products = []
    for i, p in enumerate(products, start=1):
        payload_products.append({
            "id": str(p.get("id", i)),
            "width": _dim_int(p["width"]),
            "height": _dim_int(p["height"]),
            "length": _dim_int(p["length"]),
            "weight": _weight_3(p["weight"]),
            "quantity": int(p.get("quantity", 1)),
            "insurance_value": _money_2(p.get("insurance_value", 1)),
        })

    body = {
        "from": {"postal_code": cep_origem},
        "to": {"postal_code": cep_destino},
        "products": payload_products,
        "options": {"receipt": False, "own_hand": False},
    }
    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(
            req, timeout=http_timeout, context=ssl.create_default_context()
        ) as resp:
            if resp.status != 200:
                raw = resp.read().decode("utf-8", errors="replace")
                raise MelhorEnvioAPIError(f"API retornou status {resp.status}")
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise MelhorEnvioAPIError(f"API retornou erro HTTP {e.code}") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, TimeoutError) or (reason and "timed out" in str(reason).lower()):
            raise MelhorEnvioAPIError("Timeout ao conectar na API de frete") from e
        raise MelhorEnvioAPIError("Falha de conexão com a API de frete") from e
    except TimeoutError as e:
        raise MelhorEnvioAPIError("Timeout ao conectar na API de frete") from e
    except OSError as e:
        raise MelhorEnvioAPIError("Falha de conexão com a API de frete") from e

    try:
        parsed_body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MelhorEnvioAPIError("Resposta inválida da API de frete") from e

    return _parse_response(parsed_body)


def _api_request(path: str, method: str = "POST", body: dict | None = None) -> dict:
    """
    Generic API request helper for Melhor Envio endpoints.

    Returns:
        Parsed JSON response as dict.

    Raises:
        MelhorEnvioAPIError: On connection/timeout or API error.
    """
    base = (os.environ.get("MELHOR_ENVIO_API_URL") or DEFAULT_API_BASE).rstrip("/")
    url = f"{base}{path}"
    token = _env("MELHOR_ENVIO_TOKEN", "")

    data = json.dumps(body).encode("utf-8") if body else None

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(
            req, timeout=REQUEST_TIMEOUT_SEC, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise MelhorEnvioAPIError(f"API retornou erro HTTP {e.code}: {raw}") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, TimeoutError) or (reason and "timed out" in str(reason).lower()):
            raise MelhorEnvioAPIError("Timeout ao conectar na API Melhor Envio") from e
        raise MelhorEnvioAPIError("Falha de conexão com a API Melhor Envio") from e
    except TimeoutError as e:
        raise MelhorEnvioAPIError("Timeout ao conectar na API Melhor Envio") from e
    except OSError as e:
        raise MelhorEnvioAPIError("Falha de conexão com a API Melhor Envio") from e

    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise MelhorEnvioAPIError("Resposta inválida da API Melhor Envio") from e


def add_to_cart(
    service_id: int,
    sender: dict[str, Any],
    recipient: dict[str, Any],
    products: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    agency_id: int | None = None,
) -> dict[str, Any]:
    """
    Adiciona uma etiqueta ao carrinho do Melhor Envio.

    Args:
        service_id: ID do serviço (1=PAC, 2=SEDEX, 3=Jadlog .Package, etc.)
        sender: Dados do remetente (name, phone, email, document, address, etc.)
        recipient: Dados do destinatário (name, phone, email, document, address, etc.)
        products: Lista de produtos [{name, quantity, unitary_value}]
        volumes: Lista de volumes [{height, width, length, weight}] — sem campo 'quantity'
        options: Opções adicionais (insurance_value, receipt, own_hand, non_commercial, etc.)
        agency_id: ID da agência de postagem — obrigatório para Jadlog; obtido no retorno da cotação.

    Returns:
        dict com 'id' do pedido no Melhor Envio e outros dados.

    Raises:
        MelhorEnvioAPIError: On API error.
    """
    merged_options: dict[str, Any] = {"insurance_value": 1, "receipt": False, "own_hand": False, "reverse": False}
    if options:
        merged_options.update(options)
    if merged_options.get("non_commercial") is None:
        merged_options["non_commercial"] = merged_options.get("invoice") is None

    body: dict[str, Any] = {
        "service": service_id,
        "from": sender,
        "to": recipient,
        "products": products,
        "volumes": volumes,
        "options": merged_options,
    }
    if agency_id is not None:
        body["agency"] = agency_id

    return _api_request(CART_PATH, "POST", body)


def checkout_cart(order_ids: list[str]) -> dict[str, Any]:
    """
    Faz checkout (pagamento) das etiquetas no carrinho.

    Args:
        order_ids: Lista de IDs dos pedidos no carrinho.

    Returns:
        dict com resultado do checkout.

    Raises:
        MelhorEnvioAPIError: On API error.
    """
    body = {"orders": order_ids}
    return _api_request(CHECKOUT_PATH, "POST", body)


def generate_labels(order_ids: list[str]) -> dict[str, Any]:
    """
    Gera as etiquetas após checkout.

    Args:
        order_ids: Lista de IDs dos pedidos.

    Returns:
        dict com URLs das etiquetas.

    Raises:
        MelhorEnvioAPIError: On API error.
    """
    body = {"orders": order_ids}
    return _api_request(GENERATE_PATH, "POST", body)


def get_tracking(order_ids: list[str]) -> dict[str, Any]:
    """
    Consulta rastreamento de pedidos.

    Args:
        order_ids: Lista de IDs dos pedidos no Melhor Envio.

    Returns:
        dict com informações de rastreamento.

    Raises:
        MelhorEnvioAPIError: On API error.
    """
    body = {"orders": order_ids}
    return _api_request(TRACKING_PATH, "POST", body)
