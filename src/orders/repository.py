import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from shared.database import get_supabase_client
from shared.supabase_utils import (
    fetch_profile_role_via_rest_with_user_jwt,
    normalize_profile_role,
)


class OrderRepository:
    def __init__(self) -> None:
        self.db = get_supabase_client()

    def get_order_by_id(self, order_id: str, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Fetch order by id. If user_id given, RLS enforces ownership."""
        q = self.db.table("orders").select("*").eq("id", order_id)
        if user_id:
            q = q.eq("user_id", user_id)
        res = q.execute()
        return res.data[0] if res.data else None

    def get_order_with_items(self, order_id: str, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Fetch order and its items. If user_id given, RLS enforces ownership."""
        order = self.get_order_by_id(order_id, user_id)
        if not order:
            return None
        res = self.db.table("order_items").select("*").eq("order_id", order_id).execute()
        order["items"] = res.data or []
        return order

    def list_orders_by_user(
        self,
        user_id: str,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List orders by user with fields for API contract."""
        start = (page - 1) * limit
        end = start + limit - 1
        res = (
            self.db.table("orders")
            .select("id, user_id, payment_status, delivery_status, total_amount, created_at, payment_method, payment_id, payer, payment_code, payment_url, payment_expiration", count="exact")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )
        return {"data": res.data or [], "count": res.count or 0}

    def list_all_orders(
        self,
        admin_user_id: str,
        page: int = 1,
        limit: int = 20,
        authorization_header: Optional[str] = None,
    ) -> dict[str, Any]:
        """List all orders (backoffice admin). Same simplified fields + user_id + user email."""
        rpc_result = self._list_all_orders_via_rpc(admin_user_id, page, limit)
        if rpc_result is not None:
            return rpc_result
        start = (page - 1) * limit
        end = start + limit - 1
        res = (
            self.db.table("orders")
            .select("id, user_id, payment_status, delivery_status, total_amount, created_at, payment_method, payment_id, payer, payment_code, payment_url, payment_expiration", count="exact")
            .order("created_at", desc=True)
            .range(start, end)
            .execute()
        )
        data = res.data or []
        if data:
            user_ids = list({o["user_id"] for o in data if o.get("user_id")})
            if user_ids:
                profiles_res = (
                    self.db.table("profiles").select("id, email").in_("id", user_ids).execute()
                )
                id_to_email = {p["id"]: p.get("email") for p in (profiles_res.data or [])}
                for o in data:
                    o["user_email"] = id_to_email.get(o.get("user_id")) if o.get("user_id") else None
            else:
                for o in data:
                    o["user_email"] = None
        if data:
            return {"data": data, "count": res.count or 0}
        rest_result = self._list_all_orders_via_rest(page, limit, authorization_header)
        if rest_result is not None:
            return rest_result
        return {"data": data, "count": res.count or 0}

    def get_profile_role(
        self, user_id: str, authorization_header: Optional[str] = None
    ) -> Optional[str]:
        """Fetch profile role by user_id (for admin check).

        Com SUPABASE_KEY anon (sem JWT do usuário), o RLS não devolve linha. Se o front
        enviar ``Authorization: Bearer <sessão>`` e existir ``SUPABASE_ANON_KEY`` no env,
        repete a mesma leitura REST que o front usa no Supabase.
        """
        normalized_user_id = (user_id or "").strip()
        if not normalized_user_id:
            return None
        res = (
            self.db.table("profiles")
            .select("role")
            .eq("id", normalized_user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return normalize_profile_role(res.data[0].get("role"))
        # Fallback REST: prefer SUPABASE_ANON_KEY, but accept SUPABASE_KEY when it's anon.
        anon_key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
        url = os.environ.get("SUPABASE_URL")
        if url and anon_key and authorization_header:
            role = fetch_profile_role_via_rest_with_user_jwt(
                url, anon_key, authorization_header, normalized_user_id
            )
            if role is not None:
                return role
        return None

    def get_profile_email(self, user_id: str) -> Optional[str]:
        """Fetch profile email by user_id (for order payload)."""
        res = self.db.table("profiles").select("email").eq("id", user_id).execute()
        return res.data[0].get("email") if res.data else None

    def get_order_items_by_ids(self, order_id: str, item_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch order_items by ids belonging to order_id."""
        if not item_ids:
            return []
        res = (
            self.db.table("order_items")
            .select("*")
            .eq("order_id", order_id)
            .in_("id", item_ids)
            .execute()
        )
        return res.data or []

    def get_order_items_all(self, order_id: str) -> list[dict[str, Any]]:
        """Fetch all order_items for an order."""
        res = self.db.table("order_items").select("*").eq("order_id", order_id).execute()
        return res.data or []

    def get_order_items_for_order_ids(
        self,
        order_ids: list[str],
        authorization_header: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch all order_items for multiple orders (for list responses)."""
        if not order_ids:
            return []
        res = (
            self.db.table("order_items")
            .select("*")
            .in_("order_id", order_ids)
            .execute()
        )
        data = res.data or []
        if data:
            return data
        rest_data = self._get_order_items_for_order_ids_via_rest(order_ids, authorization_header)
        return rest_data if rest_data is not None else data

    def insert_refund_request(
        self,
        order_id: str,
        request_type: str,
        amount: float,
        order_item_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Insert a refund request (customer or backoffice)."""
        row = {
            "order_id": order_id,
            "request_type": request_type,
            "status": "pending",
            "amount": amount,
            "order_item_ids": order_item_ids or [],
        }
        res = self.db.table("order_refunds").insert(row).execute()
        if not res.data:
            raise Exception("Falha ao criar solicitação de reembolso")
        return res.data[0]

    def update_refund_request(
        self,
        refund_id: str,
        status: str,
        refund_method: Optional[str] = None,
        mp_refund_id: Optional[str] = None,
        voucher_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update refund request after processing."""
        data = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if refund_method is not None:
            data["refund_method"] = refund_method
        if mp_refund_id is not None:
            data["mp_refund_id"] = mp_refund_id
        if voucher_id is not None:
            data["voucher_id"] = voucher_id
        res = self.db.table("order_refunds").update(data).eq("id", refund_id).execute()
        if not res.data:
            raise Exception("Falha ao atualizar solicitação de reembolso")
        return res.data[0]

    def update_order_delivery_status(self, order_id: str, delivery_status: str) -> dict[str, Any]:
        """Update delivery status (e.g. shipped, delivered, cancelled)."""
        res = (
            self.db.table("orders")
            .update({"delivery_status": delivery_status, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", order_id)
            .execute()
        )
        if not res.data:
            raise Exception("Falha ao atualizar status do pedido")
        return res.data[0]

    def create_voucher(self, code: str, amount: float, order_id: Optional[str], valid_until: str) -> dict[str, Any]:
        """Create a voucher (5-char code, amount, validity)."""
        row = {
            "code": code,
            "amount": amount,
            "order_id": order_id,
            "valid_until": valid_until,
        }
        res = self.db.table("vouchers").insert(row).execute()
        if not res.data:
            raise Exception("Falha ao criar voucher")
        return res.data[0]

    def get_voucher_by_code(self, code: str) -> Optional[dict[str, Any]]:
        """Fetch voucher by code."""
        res = self.db.table("vouchers").select("*").eq("code", code).execute()
        return res.data[0] if res.data else None

    def list_refund_requests_by_order(self, order_id: str) -> list[dict[str, Any]]:
        """List refund requests for an order."""
        res = self.db.table("order_refunds").select("*").eq("order_id", order_id).order("created_at", desc=True).execute()
        return res.data or []

    def _rest_headers(self, authorization_header: Optional[str] = None) -> Optional[dict[str, str]]:
        api_key = (
            os.environ.get("SUPABASE_ANON_KEY")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_KEY")
        )
        if not api_key:
            return None
        auth = (authorization_header or "").strip()
        if auth and not auth.lower().startswith("bearer "):
            auth = ""
        return {
            "apikey": api_key,
            "Authorization": auth or f"Bearer {api_key}",
        }

    def _list_all_orders_via_rest(
        self,
        page: int,
        limit: int,
        authorization_header: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        url = os.environ.get("SUPABASE_URL")
        headers = self._rest_headers(authorization_header)
        if not url or not headers:
            return None
        start = (page - 1) * limit
        try:
            orders_res = requests.get(
                f"{url.rstrip('/')}/rest/v1/orders",
                params={
                    "select": "id,user_id,payment_status,delivery_status,total_amount,created_at,payment_method,payment_id,payer,payment_code,payment_url,payment_expiration",
                    "order": "created_at.desc",
                    "limit": limit,
                    "offset": start,
                },
                headers={**headers, "Prefer": "count=exact"},
                timeout=15,
            )
            if orders_res.status_code != 200:
                return None
            data = orders_res.json() or []
            total = 0
            content_range = orders_res.headers.get("Content-Range", "")
            if "/" in content_range:
                try:
                    total = int(content_range.split("/")[-1])
                except ValueError:
                    total = 0
            self._attach_user_emails_via_rest(data, headers, url)
            return {"data": data, "count": total}
        except Exception:
            return None

    def _list_all_orders_via_rpc(
        self,
        admin_user_id: str,
        page: int,
        limit: int,
    ) -> Optional[dict[str, Any]]:
        try:
            response = self.db.rpc(
                "backoffice_list_orders",
                {
                    "p_admin_user_id": admin_user_id,
                    "p_page": page,
                    "p_limit": limit,
                },
            )
            rows = response.data or []
            total = int(rows[0]["total_count"]) if rows else 0
            data = []
            for row in rows:
                item = dict(row)
                item.pop("total_count", None)
                data.append(item)
            return {"data": data, "count": total}
        except Exception:
            return None

    def _attach_user_emails_via_rest(
        self,
        orders: list[dict[str, Any]],
        headers: dict[str, str],
        url: str,
    ) -> None:
        if not orders:
            return
        user_ids = sorted({o["user_id"] for o in orders if o.get("user_id")})
        if not user_ids:
            for order in orders:
                order["user_email"] = None
            return
        try:
            profiles_res = requests.get(
                f"{url.rstrip('/')}/rest/v1/profiles",
                params={
                    "select": "id,email",
                    "id": f"in.({','.join(user_ids)})",
                },
                headers=headers,
                timeout=15,
            )
            if profiles_res.status_code != 200:
                return
            id_to_email = {p["id"]: p.get("email") for p in (profiles_res.json() or [])}
            for order in orders:
                order["user_email"] = id_to_email.get(order.get("user_id")) if order.get("user_id") else None
        except Exception:
            return

    def _get_order_items_for_order_ids_via_rest(
        self,
        order_ids: list[str],
        authorization_header: Optional[str] = None,
    ) -> Optional[list[dict[str, Any]]]:
        url = os.environ.get("SUPABASE_URL")
        headers = self._rest_headers(authorization_header)
        if not url or not headers or not order_ids:
            return None
        try:
            res = requests.get(
                f"{url.rstrip('/')}/rest/v1/order_items",
                params={
                    "select": "*",
                    "order_id": f"in.({','.join(order_ids)})",
                },
                headers=headers,
                timeout=15,
            )
            if res.status_code != 200:
                return None
            return res.json() or []
        except Exception:
            return None
