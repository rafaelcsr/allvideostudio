import pytest
from fastapi.testclient import TestClient

from studio.api import User, create_app
from studio.config import Settings
from studio.db import MemoryDB, record_usage
from studio.storage import LocalStorage

ALICE = User(id="u-alice", email="alice@example.com")
BOB = User(id="u-bob", email="bob@example.com")
TOKENS = {"ta": ALICE, "tb": BOB}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "alice@example.com,bob@example.com")
    monkeypatch.setenv("MONTHLY_BUDGET_BRL", "100")
    monkeypatch.setenv("USD_BRL", "5")
    s = Settings()
    db, st = MemoryDB(), LocalStorage(tmp_path / "r2")
    spawned = []
    app = create_app(s, db, st, spawn=lambda jid: spawned.append(jid) or "call-1", auth=lambda tok: TOKENS.get(tok))
    return TestClient(app), db, st, spawned


def H(tok="ta"):
    return {"Authorization": f"Bearer {tok}"}


def upload(client, st, tok="ta", duration=300):
    r = client.post("/api/projects", headers=H(tok), json={
        "title": "Aula", "filename": "aula 1.mp4", "size": 1000, "content_type": "video/mp4", "duration": duration})
    assert r.status_code == 200, r.text
    p = r.json()["project"]
    st.put_bytes(p["source_key"], b"x" * 1000)
    assert client.post(f"/api/projects/{p['id']}/uploaded", headers=H(tok)).status_code == 200
    return p


def test_requires_auth_and_allowlist(env, monkeypatch):
    client, *_ = env
    assert client.get("/api/budget").status_code == 401
    assert client.get("/api/budget", headers=H("bad")).status_code == 401
    TOKENS["tx"] = User("u-x", "intruso@example.com")
    assert client.get("/api/budget", headers=H("tx")).status_code == 403


def test_upload_estimate_and_job_dispatch(env):
    client, db, st, spawned = env
    p = upload(client, st)
    assert p["source_key"].startswith("users/u-alice/projects/")
    est = client.post(f"/api/projects/{p['id']}/estimate", headers=H(), json={"style": "dinamico", "shorts": 2}).json()
    assert est["allowed"] and est["estimate"]["total_usd"] > 0
    r = client.post(f"/api/projects/{p['id']}/jobs", headers=H(), json={"style": "dinamico", "shorts": 2})
    assert r.status_code == 200, r.text
    assert spawned == [r.json()["job"]["id"]]


def test_budget_blocks_job(env):
    client, db, st, spawned = env
    p = upload(client, st)
    record_usage(db, "u-alice", None, "anthropic", 1, "tokens", 19.99, "gasto simulado")  # cap = 100/5 = $20
    r = client.post(f"/api/projects/{p['id']}/jobs", headers=H(), json={})
    assert r.status_code == 402 and spawned == []


def test_users_cannot_see_each_other(env):
    client, db, st, _ = env
    p = upload(client, st, "ta")
    assert client.post(f"/api/projects/{p['id']}/jobs", headers=H("tb"), json={}).status_code == 404


def test_rejects_oversized_and_invalid(env):
    client, *_ = env
    r = client.post("/api/projects", headers=H(), json={
        "title": "x", "filename": "x.mp4", "size": 10, "content_type": "video/mp4", "duration": 99999})
    assert r.status_code == 413
    r = client.post("/api/projects", headers=H(), json={
        "title": "x", "filename": "x.exe", "size": 10, "content_type": "application/octet-stream", "duration": 10})
    assert r.status_code == 415


def test_revise_requires_done_job(env):
    client, db, st, spawned = env
    p = upload(client, st)
    job = client.post(f"/api/projects/{p['id']}/jobs", headers=H(), json={}).json()["job"]
    assert client.post(f"/api/jobs/{job['id']}/revise", headers=H(), json={"instructions": "corte a introdução"}).status_code == 409
    db.update("jobs", job["id"], {"status": "done"})
    r = client.post(f"/api/jobs/{job['id']}/revise", headers=H(), json={"instructions": "corte a introdução"})
    assert r.status_code == 200, r.text
    assert r.json()["job"]["options"]["revision"] == "corte a introdução"
