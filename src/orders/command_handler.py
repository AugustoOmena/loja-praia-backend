from dataclasses import dataclass
from typing import Optional


def _is_backoffice(headers: dict) -> bool:
    return headers.get("x-backoffice", "").lower() == "true" or headers.get("X-Backoffice", "").lower() == "true"


def _require_order_id(order_id: str) -> str:
    if not order_id or not str(order_id).strip():
        raise ValueError("order_id obrigatório")
    return order_id.strip()


def _parse_positive_int(raw_value: Optional[str], field_name: str, default: int) -> int:
    if raw_value in (None, ""):
        return default
    value = int(raw_value)
    if value <= 0:
        raise ValueError(f"{field_name} deve ser maior que zero")
    return value


@dataclass(frozen=True)
class ListOrdersCommand:
    user_id: Optional[str]
    page: int
    limit: int
    is_backoffice: bool
    authorization_header: Optional[str]

    @classmethod
    def from_event(cls, *, query_params: dict, headers: dict, authorization_header: Optional[str]) -> "ListOrdersCommand":
        return cls(
            user_id=query_params.get("user_id"),
            page=_parse_positive_int(query_params.get("page"), "page", 1),
            limit=_parse_positive_int(query_params.get("limit"), "limit", 20),
            is_backoffice=_is_backoffice(headers),
            authorization_header=authorization_header,
        )


@dataclass(frozen=True)
class GetOrderDetailCommand:
    order_id: str
    user_id: str
    is_backoffice: bool
    authorization_header: Optional[str]

    @classmethod
    def from_event(
        cls,
        *,
        order_id: str,
        query_params: dict,
        body: dict,
        headers: dict,
        authorization_header: Optional[str],
    ) -> "GetOrderDetailCommand":
        user_id = query_params.get("user_id") or body.get("user_id")
        if not user_id:
            raise ValueError("user_id obrigatório para ver detalhe do pedido")
        return cls(
            order_id=_require_order_id(order_id),
            user_id=user_id,
            is_backoffice=_is_backoffice(headers),
            authorization_header=authorization_header,
        )


@dataclass(frozen=True)
class RequestCancelOrRefundCommand:
    order_id: str
    user_id: str

    @classmethod
    def from_event(cls, *, order_id: str, query_params: dict, body: dict) -> "RequestCancelOrRefundCommand":
        user_id = query_params.get("user_id") or body.get("user_id")
        if not user_id:
            raise ValueError("user_id obrigatório")
        return cls(order_id=_require_order_id(order_id), user_id=user_id)


@dataclass(frozen=True)
class UpdateOrderCommand:
    order_id: str
    is_backoffice: bool
    normalized_body: dict

    @classmethod
    def from_event(cls, *, order_id: str, headers: dict, body: dict) -> "UpdateOrderCommand":
        normalized = dict(body)
        if "delivery_status" not in normalized and "status" in normalized:
            normalized["delivery_status"] = normalized["status"]
        return cls(
            order_id=_require_order_id(order_id),
            is_backoffice=_is_backoffice(headers),
            normalized_body=normalized,
        )
