from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from commands._base import parse_positive_int, require_user_id


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
