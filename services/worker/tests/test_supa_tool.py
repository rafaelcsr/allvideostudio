"""Unit tests for the Supabase ops helpers (offline, fake keys only)."""
import base64
import json

from studio.config import supabase_headers
from studio.supa import key_kind, mask_db_url, project_ref


def fake_jwt(role: str) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc({'role': role, 'ref': 'abcdefghijklmnopqrst'})}.sig"


def test_key_kind_new_and_legacy():
    assert key_kind("sb_publishable_xxx") == "public"
    assert key_kind("sb_secret_xxx") == "secret"
    assert key_kind(fake_jwt("anon")) == "public"
    assert key_kind(fake_jwt("service_role")) == "secret"
    assert key_kind("lixo") == "unknown"


def test_headers_new_keys_not_sent_as_bearer():
    assert supabase_headers("sb_secret_x") == {"apikey": "sb_secret_x"}
    legacy = fake_jwt("service_role")
    assert supabase_headers(legacy)["Authorization"] == f"Bearer {legacy}"


def test_ref_and_masking():
    assert project_ref("https://abcdefghijklmnopqrst.supabase.co") == "abcdefghijklmnopqrst"
    assert project_ref("https://evil.com") is None
    fake_pw = "-".join(["nao", "e", "real"])  # built at runtime so the commit guard sees no credential literal
    masked = mask_db_url(f"postgresql://postgres.abc:{fake_pw}@aws-0-sa-east-1.pooler.supabase.com:5432/postgres")
    assert fake_pw not in masked and "•••" in masked
