"""Supabase operations CLI — never prints secret values.

  python -m studio.supa check            valida o .env (formato e tipo das chaves) sem exibir valores
  python -m studio.supa check --live     + conexões reais (Auth, REST, Postgres)
  python -m studio.supa migrate --dry-run  lista migrations pendentes
  python -m studio.supa migrate          aplica pendentes (transação por arquivo, histórico do Supabase CLI)
  python -m studio.supa verify           confere RLS, policies, grants e realtime no banco
  python -m studio.supa web-env          gera apps/web/.env.local só com valores públicos

Reads ../../.env (repo root) unless the variables are already in the environment.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
APP_TABLES = ("projects", "jobs", "renders", "usage_ledger", "budget_alerts")

OK, WARN, ERR = "OK  ", "AVISO", "ERRO"


def load_dotenv(path: Path = ROOT / ".env", override: bool = False) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if not m or line.lstrip().startswith("#"):
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if override or not os.environ.get(k):
            os.environ[k] = v
    return True


def _first(*names: str) -> tuple[str, str]:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return n, v
    return names[0], ""


def jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def key_kind(key: str) -> str:
    """'public' | 'secret' | 'unknown' — decided by format, value never shown."""
    if key.startswith("sb_publishable_"):
        return "public"
    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("eyJ"):
        role = jwt_claims(key).get("role")
        return {"anon": "public", "service_role": "secret"}.get(role, "unknown")
    return "unknown"


def project_ref(url: str) -> str | None:
    m = re.match(r"^https://([a-z0-9]{20})\.supabase\.co/?$", url.strip())
    if not m or len(set(m.group(1))) < 4:  # rejects the .env.example placeholder (xxxx…)
        return None
    return m.group(1)


def mask_db_url(url: str) -> str:
    u = urlparse(url)
    user = (u.username or "?")
    return f"{u.scheme}://{user}:•••@{u.hostname}:{u.port or 5432}{u.path}"


class Report:
    def __init__(self) -> None:
        self.errors = 0

    def line(self, level: str, msg: str) -> None:
        if level == ERR:
            self.errors += 1
        print(f"[{level}] {msg}")


def check_static(rep: Report) -> dict:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    ref = project_ref(url)
    if not url:
        rep.line(ERR, "SUPABASE_URL ausente")
    elif not ref:
        rep.line(ERR, "SUPABASE_URL deve ser https://<ref de 20 caracteres>.supabase.co")
    else:
        rep.line(OK, f"SUPABASE_URL -> projeto {ref}")

    pub_name, pub = _first("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY")
    sec_name, sec = _first("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    for name, val, want in ((pub_name, pub, "public"), (sec_name, sec, "secret")):
        if not val:
            rep.line(ERR, f"{name} ausente")
            continue
        kind = key_kind(val)
        if kind != want:
            rep.line(ERR, f"{name} parece ser uma chave '{kind}', esperado '{want}' — chaves trocadas?")
            continue
        fmt = "nova (sb_…)" if val.startswith("sb_") else "legada (JWT)"
        extra = ""
        if val.startswith("eyJ") and ref and jwt_claims(val).get("ref") not in (None, ref):
            rep.line(ERR, f"{name} pertence a outro projeto")
            continue
        rep.line(OK, f"{name}: chave {want}, formato {fmt}{extra}")
    if pub and sec and pub == sec:
        rep.line(ERR, "chave pública e secreta são iguais")

    for n in ("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY"):
        v = os.environ.get(n, "")
        if v and key_kind(v) == "secret":
            rep.line(ERR, f"{n} contém uma chave SECRETA — nunca exponha no frontend")

    db = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db:
        rep.line(WARN, "SUPABASE_DB_URL ausente (necessária só para aplicar migrations)")
    else:
        u = urlparse(db)
        if u.scheme not in ("postgres", "postgresql") or not u.password:
            rep.line(ERR, "SUPABASE_DB_URL inválida (esperado postgresql://usuario:senha@host:porta/postgres)")
        elif "[YOUR-PASSWORD]" in db or "YOUR-PASSWORD" in db:
            rep.line(ERR, "SUPABASE_DB_URL ainda contém [YOUR-PASSWORD] — substitua pela senha do banco")
        else:
            if ref and ref not in db:
                rep.line(WARN, "SUPABASE_DB_URL não menciona o ref do projeto — confira se é o mesmo projeto")
            pooled = "pooler.supabase.com" in (u.hostname or "")
            rep.line(OK, f"SUPABASE_DB_URL {mask_db_url(db)} ({'pooler IPv4' if pooled else 'conexão direta'})")
            if pooled and u.port == 6543:
                rep.line(WARN, "porta 6543 (transaction pooler): prefira 5432 (session pooler) para migrations")
    return {"url": url, "pub": pub, "sec": sec, "db": db}


def check_live(rep: Report, cfg: dict) -> None:
    import httpx

    from studio.config import supabase_headers

    url, pub, sec = cfg["url"], cfg["pub"], cfg["sec"]
    if not (url and pub and sec):
        rep.line(ERR, "verificação ao vivo exige URL e as duas chaves")
        return
    with httpx.Client(timeout=20) as h:
        r = h.get(f"{url}/auth/v1/settings", headers={"apikey": pub})
        if r.status_code == 200:
            s = r.json()
            rep.line(OK, f"Auth acessível com a chave pública (e-mail habilitado: {s.get('external', {}).get('email')})")
            if not s.get("disable_signup"):
                rep.line(WARN, "cadastro público ATIVO — desative em Authentication > Sign In / Providers > 'Allow new users to sign up'")
            else:
                rep.line(OK, "cadastro público desativado")
        else:
            rep.line(ERR, f"Auth respondeu {r.status_code} com a chave pública")

        r = h.get(f"{url}/auth/v1/admin/users", params={"per_page": 1}, headers=supabase_headers(sec))
        rep.line(OK if r.status_code == 200 else ERR, f"Auth admin com a chave secreta: HTTP {r.status_code}")

        r = h.get(f"{url}/rest/v1/projects", params={"select": "id", "limit": 1}, headers=supabase_headers(sec))
        if r.status_code == 200:
            rep.line(OK, "REST: tabela projects existe (schema aplicado)")
        elif r.status_code == 404:
            rep.line(WARN, "REST: tabela projects não existe — rode 'migrate'")
        else:
            rep.line(ERR, f"REST com chave secreta: HTTP {r.status_code} {r.text[:120]}")

        r = h.get(f"{url}/rest/v1/projects", params={"select": "id", "limit": 1}, headers={"apikey": pub})
        if r.status_code in (401, 403) or (r.status_code == 200 and r.json() == []):
            rep.line(OK, f"REST anônimo não lê projetos (HTTP {r.status_code})")
        elif r.status_code == 404:
            pass
        else:
            rep.line(ERR, f"REST anônimo retornou dados! HTTP {r.status_code}")

    if cfg["db"]:
        try:
            with connect(cfg["db"]) as conn:
                v = conn.execute("select current_setting('server_version')").fetchone()[0]
                rep.line(OK, f"Postgres conectado (versão {v})")
        except Exception as e:  # noqa: BLE001
            rep.line(ERR, f"Postgres: {type(e).__name__}: {_scrub(str(e), cfg['db'])[:200]}")


def _scrub(msg: str, db_url: str) -> str:
    pw = urlparse(db_url).password
    return msg.replace(pw, "•••") if pw else msg


def connect(db_url: str):
    import psycopg

    return psycopg.connect(db_url, connect_timeout=15, sslmode="require", autocommit=False,
                           application_name="ai-video-studio-migrate", prepare_threshold=None)


def migration_files() -> list[tuple[str, str, Path]]:
    out = []
    for p in sorted(MIGRATIONS.glob("*.sql")):
        m = re.match(r"^(\d+)_(.+)\.sql$", p.name)
        if m:
            out.append((m.group(1), m.group(2), p))
    return out


def migrate(dry_run: bool) -> int:
    db = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db:
        print("[ERRO] SUPABASE_DB_URL ausente no .env")
        return 2
    try:
        conn = connect(db)
    except Exception as e:  # noqa: BLE001
        print(f"[ERRO] conexão: {type(e).__name__}: {_scrub(str(e), db)[:200]}")
        return 2
    with conn:
        conn.execute("create schema if not exists supabase_migrations")
        conn.execute(
            "create table if not exists supabase_migrations.schema_migrations "
            "(version text primary key, statements text[], name text)"
        )
        conn.commit()
        applied = {r[0] for r in conn.execute("select version from supabase_migrations.schema_migrations")}
        pending = [m for m in migration_files() if m[0] not in applied]
        print(f"aplicadas: {len(applied)} | pendentes: {len(pending)}")
        for version, name, path in pending:
            print(f"  - {version}_{name}")
        if dry_run or not pending:
            return 0
        for version, name, path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "insert into supabase_migrations.schema_migrations (version, statements, name) values (%s, %s, %s)",
                        (version, [sql], name),
                    )
                print(f"[OK  ] aplicada {version}_{name}")
            except Exception as e:  # noqa: BLE001
                print(f"[ERRO] {version}_{name} revertida: {type(e).__name__}: {str(e)[:300]}")
                return 1
    return 0


def verify() -> int:
    rep = Report()
    db = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db:
        rep.line(ERR, "SUPABASE_DB_URL ausente")
        return 1
    with connect(db) as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
        rls = dict(q("select relname, relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                     "where n.nspname='public' and relname = any(%s)", list(APP_TABLES)))
        for t in APP_TABLES:
            if t not in rls:
                rep.line(ERR, f"tabela {t} não existe")
            else:
                rep.line(OK if rls[t] else ERR, f"RLS {'ativo' if rls[t] else 'DESATIVADO'} em {t}")
        pols = q("select tablename, policyname, cmd from pg_policies where schemaname='public'")
        bad = [p for p in pols if p[2] != "SELECT"]
        rep.line(OK if not bad else ERR, f"{len(pols)} policies, todas somente leitura" if not bad else f"policies de escrita: {bad}")
        grants = q("select grantee, table_name, privilege_type from information_schema.role_table_grants "
                   "where table_schema='public' and grantee in ('anon','authenticated') and table_name = any(%s)",
                   list(APP_TABLES) + ["monthly_spend"])
        anon = [g for g in grants if g[0] == "anon"]
        writes = [g for g in grants if g[0] == "authenticated" and g[2] != "SELECT"]
        rep.line(OK if not anon else ERR, "anon sem privilégios nas tabelas do app" if not anon else f"anon tem: {anon}")
        rep.line(OK if not writes else ERR, "authenticated apenas SELECT" if not writes else f"authenticated pode escrever: {writes}")
        pub = {r[0] for r in q("select tablename from pg_publication_tables where pubname='supabase_realtime'")}
        need = {"jobs", "renders", "projects"}
        rep.line(OK if need <= pub else ERR, f"realtime publicado para {sorted(need & pub)}")
        sp = q("select proconfig from pg_proc where proname='touch_updated_at'")
        rep.line(OK if sp and sp[0][0] and any("search_path" in c for c in sp[0][0]) else WARN, "touch_updated_at com search_path fixo")
    print("verificação:", "APROVADA" if rep.errors == 0 else f"{rep.errors} problema(s)")
    return 0 if rep.errors == 0 else 1


def web_env() -> int:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    _, pub = _first("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY")
    if not url or not pub:
        print("[ERRO] SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY são necessários")
        return 1
    if key_kind(pub) != "public":
        print("[ERRO] a chave para o frontend não é pública — abortado")
        return 1
    api = os.environ.get("API_URL", "http://localhost:8000").strip()
    target = ROOT / "apps" / "web" / ".env.local"
    target.write_text(
        "# Gerado por `python -m studio.supa web-env` — somente valores PÚBLICOS. Não versionado.\n"
        f"NEXT_PUBLIC_SUPABASE_URL={url}\nNEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY={pub}\nNEXT_PUBLIC_API_URL={api}\n",
        encoding="utf-8",
    )
    print(f"[OK  ] {target.relative_to(ROOT)} gerado (URL, chave pública, API)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="studio.supa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--live", action="store_true")
    m = sub.add_parser("migrate")
    m.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify")
    sub.add_parser("web-env")
    args = ap.parse_args(argv)
    if load_dotenv():
        print(f"(.env carregado de {ROOT / '.env'})")
    if args.cmd == "check":
        rep = Report()
        cfg = check_static(rep)
        if args.live and rep.errors == 0:
            check_live(rep, cfg)
        print("resultado:", "OK" if rep.errors == 0 else f"{rep.errors} erro(s)")
        return 0 if rep.errors == 0 else 1
    if args.cmd == "migrate":
        return migrate(args.dry_run)
    if args.cmd == "verify":
        return verify()
    return web_env()


if __name__ == "__main__":
    sys.exit(main())
