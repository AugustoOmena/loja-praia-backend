from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class CancelRequestInput(BaseModel):
    """Solicitação de cancelamento/reembolso pelo cliente (total ou por itens)."""
    total: bool = Field(default=False, description="Cancelamento total do pedido")
    order_item_ids: Optional[List[str]] = Field(default=None, description="IDs dos itens para reembolso parcial")

    @model_validator(mode="after")
    def check_partial_or_total(self):
        if self.total and self.order_item_ids:
            raise ValueError("Envie total=true ou order_item_ids, não ambos")
        if not self.total and (not self.order_item_ids or len(self.order_item_ids) == 0):
            raise ValueError("Para reembolso parcial informe order_item_ids")
        return self


class BackofficeCancelInput(BaseModel):
    """Cancelamento/reembolso pelo backoffice: valor livre (fluxo principal) ou legado por itens/full_cancel."""
    refund_method: str = Field(..., description="'mp' (Mercado Pago) ou 'voucher' (mantido no backend; UI backoffice só mp)")
    refund_amount: Optional[float] = Field(
        default=None,
        description="Valor a reembolsar (R$); teto = total_amount - shipping_amount - já reembolsado (status refunded). Sem vínculo a itens.",
    )
    cancel_item_ids: Optional[List[str]] = Field(default=None, description="Legado: IDs dos order_items a cancelar")
    full_cancel: bool = Field(default=False, description="Legado: reembolsa o saldo de mercadoria restante e pode marcar entrega cancelada")

    @field_validator("refund_method")
    @classmethod
    def refund_method_value(cls, v: str) -> str:
        if v not in ("mp", "voucher"):
            raise ValueError("refund_method deve ser 'mp' ou 'voucher'")
        return v

    @model_validator(mode="after")
    def refund_mode_requires_one_source(self):
        if self.refund_amount is not None:
            if self.refund_amount <= 0:
                raise ValueError("refund_amount deve ser maior que zero")
            if self.full_cancel or (self.cancel_item_ids and len(self.cancel_item_ids) > 0):
                raise ValueError("Não combine refund_amount com full_cancel ou cancel_item_ids")
            return self
        if self.full_cancel:
            return self
        if self.cancel_item_ids and len(self.cancel_item_ids) > 0:
            return self
        raise ValueError("Informe refund_amount ou full_cancel=true ou cancel_item_ids")


class OrderStatusUpdate(BaseModel):
    """Atualização de status de entrega do pedido pelo backoffice."""
    delivery_status: str = Field(..., min_length=1, description="Novo status de entrega (ex: pending, in_process, shipped, delivered, cancelled)")
