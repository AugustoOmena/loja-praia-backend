from typing import Optional


def require_user_id(value: Optional[str], msg: str) -> str:
    if not value or not str(value).strip():
        raise ValueError(msg)
    return value.strip()


def require_order_id(order_id: str) -> str:
    if not order_id or not str(order_id).strip():
        raise ValueError("order_id obrigatório")
    return order_id.strip()


def parse_positive_int(raw: Optional[str], field_name: str, default: int) -> int:
    if raw in (None, ""):
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{field_name} deve ser maior que zero")
    return value
