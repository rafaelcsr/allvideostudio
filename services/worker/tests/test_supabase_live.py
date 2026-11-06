"""Integração REAL com o Supabase (sem mocks). Roda só quando o .env tem as credenciais:

    .venv\\Scripts\\python.exe -m pytest -m live -v

Cria 2 usuários temporários (senhas aleatórias em memória, nunca exibidas), valida
persistência, RLS, bloqueio de escrita do navegador e verificação de tokens, e remove tudo."""
import secrets
import uuid

import httpx
import pytest

from studio.supa import key_kind, load_dotenv

load_dotenv()

from studio.api import supabase_auth  # noqa: E402
from studio.config import Settings, supabase_headers  # noqa: E402
from studio.db import SupabaseDB, month_spend_usd, record_usage  # noqa: E402

S = Settings()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (S.supabase_url and S.supabase_anon_key and S.supabase_service_key),
                       reason="credenciais do Supabase ausentes no .env"),
]


@pytest.fixture(scope="module")
def admin():
    with httpx.Client(base_url=S.supabase_url, timeout=30, headers=supabase_headers(S.supabase_service_key)) as c:
        yield c


def _token(email: str, password: str) -> str:
    r = httpx.post(f"{S.supabase_url}/auth/v1/token", params={"grant_type": "password"},
                   headers={"apikey": S.supabase_anon_key}, json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login falhou: HTTP {r.status_code}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def users(admin):
    ids: list[str] = []
    out: list[dict] = []
    try:
        for label in ("a", "b"):
            email = f"avs-test-{label}-{uuid.uuid4().hex[:8]}@example.com"
            pw = secrets.token_urlsafe(24)
            r = admin.post("/auth/v1/admin/users", json={"email": email, "password": pw, "email_confirm": True})
            assert r.status_code in (200, 201), f"criar usuário: HTTP {r.status_code} {r.text[:200]}"
            ids.append(r.json()["id"])
            out.append({"id": ids[-1], "email": email, "token": _token(email, pw)})
        yield out
    finally:
        for uid in ids:  # ledger first (FK is set null, so delete explicitly), then the user (cascades the rest)
            admin.delete("/rest/v1/usage_ledger", params={"user_id": f"eq.{uid}"})
            admin.delete(f"/auth/v1/admin/users/{uid}")


def as_user(token: str | None) -> dict:
    h = {"apikey": S.supabase_anon_key}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def rest(path: str, headers: dict, method: str = "GET", **kw) -> httpx.Response:
    return httpx.request(method, f"{S.supabase_url}/rest/v1/{path}", headers=headers, timeout=30, **kw)


def test_key_types_are_correct():
    assert key_kind(S.supabase_anon_key) == "public"
    assert key_kind(S.supabase_service_key) == "secret"


def test_persistence_with_service_key(users):
    a = users[0]
    db = SupabaseDB(S)
    p = db.insert("projects", {"user_id": a["id"], "title": "teste integração", "status": "uploaded", "declared_duration": 12.5})
    assert p["id"] and p["status"] == "uploaded"
    before = p["updated_at"]
    db.update("projects", p["id"], {"status": "ready"})
    got = db.get("projects", p["id"])
    assert got["status"] == "ready" and got["updated_at"] > before  # trigger touch_updated_at
    job = db.insert("jobs", {"project_id": p["id"], "user_id": a["id"], "options": {"style": "dinamico"}, "estimate_usd": 0.12})
    assert job["status"] == "queued" and job["options"]["style"] == "dinamico"
    rows = db.select("jobs", eq={"project_id": p["id"]}, order="created_at.desc", limit=5)
    assert [r["id"] for r in rows] == [job["id"]]
    r = db.insert("renders", {"job_id": job["id"], "project_id": p["id"], "user_id": a["id"], "kind": "full",
                              "r2_key": "users/x/y.mp4", "qc": {"ok": True}})
    assert r["qc"]["ok"] is True


def test_usage_ledger_and_month_spend(users):
    db = SupabaseDB(S)
    before = month_spend_usd(db)
    record_usage(db, users[0]["id"], None, "anthropic", 10, "tokens", 0.000123, "teste integração")
    assert month_spend_usd(db) == pytest.approx(before + 0.000123, abs=1e-6)


def test_rls_isolates_users(users):
    a, b = users
    SupabaseDB(S).insert("projects", {"user_id": a["id"], "title": "só do A", "status": "uploaded"})
    ra = rest("projects?select=id,title,user_id", as_user(a["token"]))
    rb = rest("projects?select=id,title,user_id", as_user(b["token"]))
    assert ra.status_code == 200 and rb.status_code == 200
    assert ra.json() and all(row["user_id"] == a["id"] for row in ra.json())
    assert rb.json() == []
    led = rest("usage_ledger?select=user_id", as_user(b["token"]))
    assert led.status_code == 200 and led.json() == []


def test_anonymous_reads_nothing():
    r = rest("projects?select=id&limit=1", as_user(None))
    assert r.status_code in (401, 403) or r.json() == []
    r = rest("usage_ledger?select=id&limit=1", as_user(None))
    assert r.status_code in (401, 403) or r.json() == []


def test_browser_cannot_write(users):
    a = users[0]
    h = {**as_user(a["token"]), "Content-Type": "application/json", "Prefer": "return=representation"}
    r = rest("projects", h, "POST", json={"user_id": a["id"], "title": "invasão", "status": "uploaded"})
    assert r.status_code in (401, 403), f"insert pelo navegador deveria falhar: HTTP {r.status_code}"
    r = rest("usage_ledger", h, "POST", json={"user_id": a["id"], "service": "x", "cost_usd": -100})
    assert r.status_code in (401, 403)
    own = rest("projects?select=id&limit=1", as_user(a["token"])).json()
    if own:
        r = rest(f"projects?id=eq.{own[0]['id']}", h, "PATCH", json={"status": "archived"})
        assert r.status_code in (401, 403) or r.json() == []


def test_api_token_verification(users):
    verify = supabase_auth(S)
    u = verify(users[0]["token"])
    assert u is not None and u.id == users[0]["id"] and u.email == users[0]["email"]
    assert verify("token-invalido") is None


def test_budget_alerts_readable_only_when_signed_in(users):
    assert rest("budget_alerts?select=id&limit=1", as_user(users[0]["token"])).status_code == 200
    r = rest("budget_alerts?select=id&limit=1", as_user(None))
    assert r.status_code in (401, 403) or r.json() == []
