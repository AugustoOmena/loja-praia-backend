from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from commands._base import require_order_id


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
