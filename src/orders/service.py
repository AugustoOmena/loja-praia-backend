import os
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import requests
from repository import OrderRepository
from schemas import (
    BackofficeCancelInput,
    BackofficeRefundByAmount,
    BackofficeRefundFull,
    CancelRequestInput,
)


ORDER_COMPLETED_STATUSES = ("approved", "completed")
CUSTOMER_CANCEL_DAYS = 7

_MONEY_EPS = 0.005


def _sum_refunded_merchandise(refunds: list[dict[str, Any]]) -> float:
    """Soma amounts já extornados com sucesso (não inclui pending)."""
    return round(
        sum(float(r.get("amount") or 0) for r in refunds if r.get("status") == "refunded"),
        2,
    )


def _merchandise_refund_cap(order: dict[str, Any], already_refunded: float) -> tuple[float, float, float]:
    """
    Retorna (restante_permitido, teto_mercadoria, ja_reembolsado).
    teto_mercadoria = total_amount - frete (frete ausente => 0).
    """
    frete = float(order.get("shipping_amount") or 0)
    total = float(order.get("total_amount") or 0)
    teto = round(max(0.0, total - frete), 2)
    ja = round(max(0.0, already_refunded), 2)
    restante = round(max(0.0, teto - ja), 2)
    return restante, teto, ja


def _attach_items_to_orders(
    repo: OrderRepository,
    orders: list[dict[str, Any]],
    authorization_header: Optional[str] = None,
) -> None:
    """Attach order_items to each order in list (mutates orders in place)."""
    if not orders:
        return
    order_ids = [o["id"] for o in orders]
    all_items = repo.get_order_items_for_order_ids(order_ids, authorization_header=authorization_header)
    by_order: dict[str, list] = {}
    for item in all_items:
        oid = item.get("order_id")
        if oid not in by_order:
            by_order[oid] = []
        by_order[oid].append(item)
    for o in orders:
        o["items"] = by_order.get(o["id"], [])
        o["shipping_address"] = (o.get("payer") or {}).get("address") if isinstance(o.get("payer"), dict) else None


def _enrich_order_payload(repo: OrderRepository, order: dict[str, Any]) -> None:
    """Add user_email and shipping_address to order for API contract (mutates in place)."""
    uid = order.get("user_id")
    if uid:
        order["user_email"] = repo.get_profile_email(uid)
    else:
        order["user_email"] = None
    order["shipping_address"] = (order.get("payer") or {}).get("address") if isinstance(order.get("payer"), dict) else None


class OrderService:
    def __init__(self) -> None:
        self.repo = OrderRepository()
        self._mp_token = os.environ.get("MP_ACCESS_TOKEN")

    def get_order_detail(self, order_id: str, user_id: str) -> dict[str, Any]:
        """Full order detail for customer (must be own order)."""
        order = self.repo.get_order_with_items(order_id, user_id=user_id)
        if not order:
            raise Exception("Pedido não encontrado")
        refunds = self.repo.list_refund_requests_by_order(order_id)
        order["refund_requests"] = refunds
        _enrich_order_payload(self.repo, order)
        return order

    def get_order_detail_for_admin(
        self,
        order_id: str,
        admin_user_id: str,
        authorization_header: Optional[str] = None,
    ) -> dict[str, Any]:
        """Full order detail for backoffice (qualquer pedido). Exige role admin como na listagem."""
        role = self.repo.get_profile_role(admin_user_id, authorization_header=authorization_header)
        if role != "admin":
            raise PermissionError("Apenas usuários com role admin podem ver detalhes de qualquer pedido")
        order = self.repo.get_order_with_items(order_id, user_id=None)
        if not order:
            raise Exception("Pedido não encontrado")
        refunds = self.repo.list_refund_requests_by_order(order_id)
        order["refund_requests"] = refunds
        _enrich_order_payload(self.repo, order)
        return order

    def list_orders_by_customer(self, user_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """List of orders by customer, each with items and user_email, shipping_address."""
        result = self.repo.list_orders_by_user(user_id, page=page, limit=limit)
        _attach_items_to_orders(self.repo, result["data"])
        user_email = self.repo.get_profile_email(user_id)
        for o in result["data"]:
            o["user_email"] = user_email
        return result

    def list_all_orders_for_admin(
        self,
        admin_user_id: str,
        page: int = 1,
        limit: int = 20,
        authorization_header: Optional[str] = None,
    ) -> dict[str, Any]:
        """List all orders; only allowed when requester has role 'admin'. Each order includes items."""
        role = self.repo.get_profile_role(admin_user_id, authorization_header=authorization_header)
        if role != "admin":
            raise PermissionError("Apenas usuários com role admin podem listar todos os pedidos")
        result = self.repo.list_all_orders(
            admin_user_id,
            page=page,
            limit=limit,
            authorization_header=authorization_header,
        )
        _attach_items_to_orders(self.repo, result["data"], authorization_header=authorization_header)
        return result

    def update_order_delivery_status(self, order_id: str, delivery_status: str) -> dict[str, Any]:
        """Backoffice: update delivery_status and return full order with items."""
        if not self.repo.get_order_by_id(order_id, user_id=None):
            raise Exception("Pedido não encontrado")
        self.repo.update_order_delivery_status(order_id, delivery_status)
        order = self.repo.get_order_with_items(order_id, user_id=None)
        order["refund_requests"] = self.repo.list_refund_requests_by_order(order_id)
        _enrich_order_payload(self.repo, order)
        return order

    def _order_completed_at(self, order: dict[str, Any]) -> Optional[datetime]:
        """Order is completed when status is approved/completed; use updated_at or created_at."""
        if order.get("payment_status") not in ORDER_COMPLETED_STATUSES:
            return None
        raw = order.get("updated_at") or order.get("created_at")
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                return None
        return raw

    def request_cancel_or_refund(self, order_id: str, user_id: str, payload: CancelRequestInput) -> dict[str, Any]:
        """
        Customer request: cancel/refund total or partial (by item), only within 7 days of completion.
        Creates a refund_request row (customer, pending); backoffice processes later.
        """
        order = self.repo.get_order_with_items(order_id, user_id=user_id)
        if not order:
            raise Exception("Pedido não encontrado")
        completed_at = self._order_completed_at(order)
        if not completed_at:
            raise Exception("Pedido ainda não está concluído; cancelamento disponível após conclusão")
        cutoff = datetime.now(timezone.utc) - timedelta(days=CUSTOMER_CANCEL_DAYS)
        if completed_at < cutoff:
            raise Exception(
                f"Solicitação de cancelamento/reembolso permitida apenas até 7 dias após a conclusão do pedido"
            )
        if payload.total:
            amount = float(order.get("total_amount", 0))
            item_ids: list[str] = []
        else:
            items = self.repo.get_order_items_by_ids(order_id, payload.order_item_ids or [])
            if len(items) != len(payload.order_item_ids or []):
                raise Exception("Um ou mais itens não pertencem a este pedido")
            amount = sum(float(i.get("price_at_purchase") or i.get("price", 0)) * int(i.get("quantity", 0)) for i in items)
            item_ids = [str(i["id"]) for i in items]
        ref = self.repo.insert_refund_request(
            order_id=order_id,
            request_type="customer",
            amount=amount,
            order_item_ids=item_ids if item_ids else None,
        )
        return {
            "message": "Solicitação de cancelamento/reembolso registrada",
            "refund_request_id": ref["id"],
            "order_id": order_id,
            "amount": amount,
            "status": "pending",
        }

    def _generate_voucher_code(self, length: int = 5) -> str:
        """5 alphanumeric uppercase."""
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    def _ensure_unique_voucher_code(self) -> str:
        for _ in range(20):
            code = self._generate_voucher_code()
            if not self.repo.get_voucher_by_code(code):
                return code
        raise Exception("Não foi possível gerar código de voucher único")

    def create_voucher(self, amount: float, order_id: Optional[str] = None, valid_days: int = 365) -> dict[str, Any]:
        """Generate voucher: 5-char code, amount, validity. Used by backoffice for refund as voucher."""
        code = self._ensure_unique_voucher_code()
        valid_until = (datetime.now(timezone.utc) + timedelta(days=valid_days)).isoformat()
        v = self.repo.create_voucher(code=code, amount=amount, order_id=order_id, valid_until=valid_until)
        return {
            "id": v["id"],
            "code": v["code"],
            "amount": float(v["amount"]),
            "order_id": v.get("order_id"),
            "created_at": v.get("created_at"),
            "valid_until": v.get("valid_until"),
        }

    def _refund_mercadopago(self, mp_payment_id: str, amount: Optional[float], idempotency_key: str) -> dict[str, Any]:
        """POST /v1/payments/{id}/refunds. Amount optional = full refund."""
        url = f"https://api.mercadopago.com/v1/payments/{mp_payment_id}/refunds"
        headers = {
            "Authorization": f"Bearer {self._mp_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }
        body = {} if amount is None else {"amount": round(amount, 2)}
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        if resp.status_code not in (200, 201):
            err = resp.json() if resp.text else {}
            msg = err.get("message", resp.text or "Erro Mercado Pago")
            raise Exception(f"Reembolso MP: {msg}")
        return resp.json()

    def backoffice_cancel_and_refund(
        self,
        order_id: str,
        payload: BackofficeCancelInput,
    ) -> dict[str, Any]:
        """
        Backoffice: reembolso MP ou voucher, com modo explícito.
        - ``mode=amount``: usa ``refund_amount``.
        - ``mode=full``: reembolsa todo o saldo de mercadoria restante.
        - ``mode=items``: soma os itens enviados em ``order_item_ids``.
        """
        order = self.repo.get_order_with_items(order_id, user_id=None)
        if not order:
            raise Exception("Pedido não encontrado")
        mp_payment_id = order.get("mp_payment_id")
        items = order.get("items") or []
        refunds_existing = self.repo.list_refund_requests_by_order(order_id)
        ja = _sum_refunded_merchandise(refunds_existing)
        rem, teto, ja_display = _merchandise_refund_cap(order, ja)

        refund_data = payload.root
        if isinstance(refund_data, BackofficeRefundByAmount):
            amount = round(float(refund_data.refund_amount), 2)
            if amount > rem + _MONEY_EPS:
                raise ValueError(
                    f"Valor acima do permitido. Máximo: R$ {rem:.2f} "
                    f"(mercadoria até R$ {teto:.2f}, já reembolsado R$ {ja_display:.2f})."
                )
            item_ids = []
        elif isinstance(refund_data, BackofficeRefundFull):
            if rem <= _MONEY_EPS:
                raise ValueError(
                    f"Não há saldo de mercadoria para reembolsar (máx. R$ {teto:.2f}, já R$ {ja_display:.2f})."
                )
            amount = rem
            item_ids = [str(i["id"]) for i in items]
        else:
            cancel_ids = refund_data.order_item_ids
            selected = self.repo.get_order_items_by_ids(order_id, cancel_ids)
            if len(selected) != len(cancel_ids):
                raise Exception("Um ou mais itens não pertencem a este pedido")
            amount = round(
                sum(
                    float(i.get("price_at_purchase") or i.get("price", 0)) * int(i.get("quantity", 0))
                    for i in selected
                ),
                2,
            )
            if amount > rem + _MONEY_EPS:
                raise ValueError(
                    f"Soma dos itens (R$ {amount:.2f}) excede o máximo reembolsável em mercadoria: R$ {rem:.2f} "
                    f"(teto R$ {teto:.2f}, já reembolsado R$ {ja_display:.2f})."
                )
            item_ids = [str(i["id"]) for i in selected]
        ref = self.repo.insert_refund_request(
            order_id=order_id,
            request_type="backoffice",
            amount=amount,
            order_item_ids=item_ids,
        )
        refund_id = ref["id"]
        if refund_data.refund_method == "mp":
            if not mp_payment_id:
                raise Exception("Pedido sem mp_payment_id; reembolso MP não disponível")
            if not self._mp_token:
                raise Exception("MP_ACCESS_TOKEN não configurado")
            idem = str(uuid.uuid4())
            mp_resp = self._refund_mercadopago(mp_payment_id, amount, idem)
            self.repo.update_refund_request(
                refund_id, status="refunded", refund_method="mp", mp_refund_id=str(mp_resp.get("id", ""))
            )
            result_refund = {"mp_refund_id": mp_resp.get("id"), "status": "refunded"}
        else:
            voucher = self.create_voucher(amount=amount, order_id=order_id)
            self.repo.update_refund_request(
                refund_id, status="refunded", refund_method="voucher", voucher_id=voucher["id"]
            )
            result_refund = {"voucher": voucher, "status": "refunded"}
        if isinstance(refund_data, BackofficeRefundFull):
            self.repo.update_order_delivery_status(order_id, "cancelled")
        return {
            "message": "Cancelamento e reembolso processados",
            "order_id": order_id,
            "refund_request_id": refund_id,
            "amount": amount,
            **result_refund,
        }
