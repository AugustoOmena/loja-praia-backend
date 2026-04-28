from __future__ import annotations

from dataclasses import dataclass

from commands._base import require_order_id, require_user_id


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
