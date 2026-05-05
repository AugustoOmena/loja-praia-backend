from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from domain.command_handlers._base import require_order_id, require_user_id


@dataclass(frozen=True)
class GetOrderDetailAdminCommand:
    order_id: str
    user_id: str
    authorization_header: Optional[str]

    @classmethod
    def from_event(
        cls,
        *,
        order_id: str,
        query_params: dict,
        body: dict,
        authorization_header: Optional[str],
    ) -> GetOrderDetailAdminCommand:
        return cls(
            order_id=require_order_id(order_id),
            user_id=require_user_id(
                query_params.get("user_id") or body.get("user_id"),
                "user_id obrigatório para ver detalhe do pedido",
            ),
            authorization_header=authorization_header,
        )


@dataclass(frozen=True)
class GetOrderDetailCommand:
    order_id: str
    user_id: str

    @classmethod
    def from_event(cls, *, order_id: str, query_params: dict, body: dict) -> GetOrderDetailCommand:
        return cls(
            order_id=require_order_id(order_id),
            user_id=require_user_id(
                query_params.get("user_id") or body.get("user_id"),
                "user_id obrigatório para ver detalhe do pedido",
            ),
        )

@dataclass(frozen=True)
class ListAllOrdersAdminCommand:
    user_id: str
    page: int
    limit: int
    authorization_header: Optional[str]

    @classmethod
    def from_event(
        cls,
        *,
        query_params: dict,
        authorization_header: Optional[str],
    ) -> ListAllOrdersAdminCommand:
        return cls(
            user_id=require_user_id(query_params.get("user_id"), "user_id obrigatório para listar pedidos"),
            page=parse_positive_int(query_params.get("page"), "page", 1),
            limit=parse_positive_int(query_params.get("limit"), "limit", 20),
            authorization_header=authorization_header,
        )

@dataclass(frozen=True)
class ListOrdersCommand:
    user_id: str
    page: int
    limit: int

    @classmethod
    def from_event(cls, *, query_params: dict) -> ListOrdersCommand:
        return cls(
            user_id=require_user_id(query_params.get("user_id"), "user_id obrigatório para listar pedidos"),
            page=parse_positive_int(query_params.get("page"), "page", 1),
            limit=parse_positive_int(query_params.get("limit"), "limit", 20),
        )


@dataclass(frozen=True)
class RequestCancelOrRefundCommand:
    order_id: str
    user_id: str

    @classmethod
    def from_event(cls, *, order_id: str, query_params: dict, body: dict) -> RequestCancelOrRefundCommand:
        return cls(
            order_id=require_order_id(order_id),
            user_id=require_user_id(
                query_params.get("user_id") or body.get("user_id"),
                "user_id obrigatório",
            ),
        )


@dataclass(frozen=True)
class UpdateDeliveryStatusCommand:
    order_id: str
    delivery_status: str


@dataclass(frozen=True)
class BackofficeRefundCommand:
    order_id: str
    raw_body: dict


def build_update_order_command(
    *,
    order_id: str,
    body: dict,
) -> Union[UpdateDeliveryStatusCommand, BackofficeRefundCommand]:
    oid = require_order_id(order_id)
    normalized = dict(body)
    if "delivery_status" not in normalized and "status" in normalized:
        normalized["delivery_status"] = normalized["status"]

    if "delivery_status" in normalized and "refund_method" not in normalized:
        delivery_status = str(normalized["delivery_status"]).strip()
        if not delivery_status:
            raise ValueError("delivery_status não pode ser vazio")
        return UpdateDeliveryStatusCommand(order_id=oid, delivery_status=delivery_status)

    if normalized.get("refund_method") is not None:
        return BackofficeRefundCommand(order_id=oid, raw_body=normalized)

    raise ValueError(
        'Envie {"delivery_status": "..."} para editar entrega '
        "ou payload de cancelamento/reembolso (refund_method, etc.)"
    )
