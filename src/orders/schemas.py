from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class CancelRequestInput(BaseModel):
    """Solicitação de cancelamento/reembolso pelo cliente (total ou por itens)."""
    total: bool = Field(default=False, description="Cancelamento total do pedido",)
    order_item_ids: Optional[List[str]] = Field(default=None, description="IDs dos itens para reembolso parcial",)

    @model_validator(mode="after")
    def check_partial_or_total(self):
        if self.total and self.order_item_ids:
            raise ValueError("Envie total=true ou order_item_ids, não ambos")
        if not self.total and (not self.order_item_ids or len(self.order_item_ids) == 0):
            raise ValueError("Para reembolso parcial informe order_item_ids")
        return self


class _BackofficeRefundBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refund_method: Literal["mp", "voucher"] = Field(..., description="'mp' (Mercado Pago) ou 'voucher'",)


class BackofficeRefundByAmount(_BackofficeRefundBase):
    mode: Literal["amount"] = "amount"
    refund_amount: float = Field(..., gt=0, description="Valor a reembolsar (R$), sem vínculo direto com itens.",)


class BackofficeRefundFull(_BackofficeRefundBase):
    mode: Literal["full"] = "full"


class BackofficeRefundByItems(_BackofficeRefundBase):
    mode: Literal["items"] = "items"
    order_item_ids: list[str] = Field(..., description="IDs dos itens do pedido para reembolso parcial.",)

    @field_validator("order_item_ids")
    @classmethod
    def order_item_ids_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("order_item_ids deve conter ao menos um item")
        return value


BackofficeRefundPayload = Annotated[
    Union[BackofficeRefundByAmount, BackofficeRefundFull, BackofficeRefundByItems],
    Field(discriminator="mode",),
]


class BackofficeCancelInput(RootModel[BackofficeRefundPayload]):
    model_config = ConfigDict(extra="forbid")


class OrderStatusUpdate(BaseModel):
    """Atualização de status de entrega do pedido pelo backoffice."""
    delivery_status: str = Field(..., min_length=1, description="Novo status de entrega (ex: pending, in_process, shipped, delivered, cancelled)",)
