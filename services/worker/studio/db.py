"""Database access: Supabase PostgREST (service role) in production, in-memory for tests."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Protocol

import httpx

from studio.config import Settings


def month_start() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def day_start() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class DB(Protocol):
    def get(self, table: str, id: str) -> dict | None: ...
    def insert(self, table: str, row: dict) -> dict: ...
    def update(self, table: str, id: str, patch: dict) -> dict: ...
    def select(self, table: str, eq: dict | None = None, gte: dict | None = None, order: str | None = None, limit: int | None = None) -> list[dict]: ...


class SupabaseDB:
    def __init__(self, s: Settings):
        self.base = f"{s.supabase_url}/rest/v1"
        self.http = httpx.Client(
            timeout=30,
            headers={
                "apikey": s.supabase_service_key,
                "Authorization": f"Bearer {s.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )

    def _check(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def get(self, table, id):
        rows = self._check(self.http.get(f"{self.base}/{table}", params={"id": f"eq.{id}", "limit": 1}))
        return rows[0] if rows else None

    def insert(self, table, row):
        return self._check(self.http.post(f"{self.base}/{table}", json=row))[0]

    def update(self, table, id, patch):
        rows = self._check(self.http.patch(f"{self.base}/{table}", params={"id": f"eq.{id}"}, json=patch))
        return rows[0] if rows else {}

    def select(self, table, eq=None, gte=None, order=None, limit=None):
        params: dict[str, Any] = {}
        for k, v in (eq or {}).items():
            params[k] = f"eq.{v}"
        for k, v in (gte or {}).items():
            params[k] = f"gte.{v}"
        if order:
            params["order"] = order
        if limit:
            params["limit"] = limit
        return self._check(self.http.get(f"{self.base}/{table}", params=params)) or []


class MemoryDB:
    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {}

    def _t(self, table):
        return self.tables.setdefault(table, {})

    def get(self, table, id):
        r = self._t(table).get(str(id))
        return dict(r) if r else None

    def insert(self, table, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("created_at", dt.datetime.now(dt.timezone.utc).isoformat())
        self._t(table)[str(row["id"])] = row
        return dict(row)

    def update(self, table, id, patch):
        r = self._t(table)[str(id)]
        r.update(patch)
        return dict(r)

    def select(self, table, eq=None, gte=None, order=None, limit=None):
        rows = [dict(r) for r in self._t(table).values()]
        rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in (eq or {}).items())]
        rows = [r for r in rows if all(str(r.get(k, "")) >= str(v) for k, v in (gte or {}).items())]
        if order:
            key, _, direction = order.partition(".")
            rows.sort(key=lambda r: str(r.get(key, "")), reverse=direction == "desc")
        return rows[:limit] if limit else rows


def month_spend_usd(db: DB) -> float:
    rows = db.select("usage_ledger", gte={"created_at": month_start()})
    return float(sum(float(r.get("cost_usd") or 0) for r in rows))


def record_usage(db: DB, user_id: str | None, job_id: str | None, service: str, quantity: float, unit: str, cost: float, description: str = "") -> None:
    db.insert("usage_ledger", {
        "user_id": user_id, "job_id": job_id, "service": service, "quantity": round(quantity, 4),
        "unit": unit, "cost_usd": round(cost, 6), "description": description,
    })
