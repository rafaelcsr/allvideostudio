"""HTTP API (FastAPI). Reads go straight from the browser to Supabase under RLS;
this API handles everything that needs secrets or spends money: upload URLs,
cost estimates, budget enforcement, job dispatch and signed download links."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from studio.budget import BudgetExceeded, check_budget, estimate_job
from studio.config import Settings
from studio.db import DB, day_start, month_spend_usd, month_start
from studio.pipeline.process import JobOptions
from studio.runner import project_prefix
from studio.storage import Storage
from studio.styles import AUTO, STYLES


@dataclass
class User:
    id: str
    email: str


AuthFn = Callable[[str], User | None]
SpawnFn = Callable[[str], str | None]


def supabase_auth(settings: Settings) -> AuthFn:
    cache: dict[str, tuple[float, User]] = {}

    def verify(token: str) -> User | None:
        hit = cache.get(token)
        if hit and hit[0] > time.time():
            return hit[1]
        r = httpx.get(f"{settings.supabase_url}/auth/v1/user",
                      headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        u = User(id=d["id"], email=(d.get("email") or "").lower())
        cache[token] = (time.time() + 60, u)
        if len(cache) > 500:
            cache.clear()
        return u

    return verify


class NewProject(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    content_type: str
    duration: float = Field(gt=0)


class JobRequest(BaseModel):
    style: str = AUTO
    full: bool = True
    full_aspect: str = "16:9"
    shorts: int = Field(default=3, ge=0, le=10)
    shorts_aspect: str = "9:16"
    shorts_style: str = "dinamico"
    hook_open: bool = False
    captions: bool = True
    remove_disfluencies: bool = True
    use_llm: bool = True
    reanalyze: bool = False


class ReviseRequest(BaseModel):
    instructions: str = Field(min_length=3, max_length=2000)
    options: JobRequest | None = None


ASPECTS = {"16:9", "9:16", "1:1"}


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:]
    return base or "video.mp4"


def create_app(settings: Settings, db: DB, storage: Storage, spawn: SpawnFn, auth: AuthFn | None = None) -> FastAPI:
    app = FastAPI(title="AI Video Studio API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"], allow_headers=["authorization", "content-type"],
    )
    auth = auth or supabase_auth(settings)
    cap_usd = settings.monthly_budget_usd

    def current_user(authorization: str = Header(default="")) -> User:
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, "não autenticado")
        user = auth(token)
        if user is None:
            raise HTTPException(401, "sessão inválida")
        if settings.allowed_emails and user.email not in settings.allowed_emails:
            raise HTTPException(403, "usuário não autorizado neste estúdio")
        return user

    def own(table: str, id: str, user: User) -> dict:
        row = db.get(table, id)
        if not row or row.get("user_id") != user.id:
            raise HTTPException(404, "não encontrado")
        return row

    def validate_opts(req: JobRequest) -> None:
        if req.style != AUTO and req.style not in STYLES:
            raise HTTPException(422, f"estilo inválido: {req.style}")
        if req.shorts_style not in STYLES:
            raise HTTPException(422, f"estilo de shorts inválido: {req.shorts_style}")
        if req.full_aspect not in ASPECTS or req.shorts_aspect not in ASPECTS:
            raise HTTPException(422, "proporção inválida")
        if req.shorts > settings.max_shorts_per_job:
            raise HTTPException(422, f"máximo de {settings.max_shorts_per_job} shorts por job")
        if not req.full and req.shorts == 0:
            raise HTTPException(422, "selecione ao menos uma saída")

    def estimate_for(project: dict, req: JobRequest, revision: bool = False):
        dur = float(project.get("duration") or project.get("declared_duration") or 0)
        has_transcript = db.select("jobs", eq={"project_id": project["id"], "status": "done"}, limit=1)
        reuse_analysis = bool(has_transcript) and not req.reanalyze and not revision
        return estimate_job(dur, full=req.full, shorts=req.shorts, use_llm=req.use_llm and not reuse_analysis,
                            transcript_cached=bool(has_transcript), revision=revision)

    def budget_state() -> dict:
        spent = month_spend_usd(db)
        return {"spent_usd": round(spent, 4), "cap_usd": round(cap_usd, 2), "cap_brl": settings.monthly_budget_brl,
                "spent_brl": round(spent * settings.usd_brl, 2), "pct": round(spent / cap_usd, 4) if cap_usd else 1}

    def dispatch(user: User, project: dict, req: JobRequest, kind: str, parent: dict | None, revision: str | None):
        today = db.select("jobs", eq={"user_id": user.id}, gte={"created_at": day_start()})
        if len(today) >= settings.max_jobs_per_day:
            raise HTTPException(429, f"limite diário de {settings.max_jobs_per_day} jobs atingido")
        running = [j for j in db.select("jobs", eq={"user_id": user.id}) if j.get("status") in ("queued", "running")]
        if len(running) >= 2:
            raise HTTPException(429, "aguarde os jobs em andamento terminarem")
        est = estimate_for(project, req, revision=bool(revision))
        try:
            alert = check_budget(month_spend_usd(db), est.total_usd, cap_usd, settings.budget_alert_pct)
        except BudgetExceeded as e:
            raise HTTPException(402, str(e)) from e
        opts = req.model_dump()
        if revision:
            opts["revision"] = revision
        job = db.insert("jobs", {
            "project_id": project["id"], "user_id": user.id, "kind": kind, "status": "queued",
            "style": req.style, "options": opts, "progress": 0, "stage": "na fila",
            "estimate_usd": round(est.total_usd, 4), "parent_job_id": parent["id"] if parent else None,
        })
        if alert:
            db.insert("budget_alerts", {"month": month_start()[:10], "level": "warning", "message": alert})
        try:
            call_id = spawn(job["id"])
        except Exception as e:  # noqa: BLE001
            db.update("jobs", job["id"], {"status": "failed", "error": f"falha ao despachar: {e}"})
            raise HTTPException(503, "processamento indisponível no momento") from e
        if call_id:
            db.update("jobs", job["id"], {"modal_call_id": call_id})
        return {"job": job, "estimate": est.to_dict(settings.usd_brl), "alert": alert}

    @app.get("/api/health")
    def health():
        return {"ok": True, "missing_config": settings.missing()}

    @app.get("/api/budget")
    def budget(user: User = Depends(current_user)):
        rows = db.select("usage_ledger", gte={"created_at": month_start()})
        by: dict[str, float] = {}
        for r in rows:
            by[r["service"]] = by.get(r["service"], 0.0) + float(r.get("cost_usd") or 0)
        return {**budget_state(), "by_service": {k: round(v, 4) for k, v in by.items()},
                "storage_gb": round(storage.total_bytes() / 1e9, 3), "storage_cap_gb": settings.storage_cap_gb}

    @app.post("/api/projects")
    def new_project(body: NewProject, user: User = Depends(current_user)):
        if not body.content_type.startswith("video/"):
            raise HTTPException(415, "envie um arquivo de vídeo")
        if body.size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"arquivo acima de {settings.max_upload_mb:.0f} MB")
        if body.duration > settings.max_video_minutes * 60:
            raise HTTPException(413, f"vídeo acima de {settings.max_video_minutes:.0f} minutos")
        used = storage.total_bytes()
        if used + body.size > settings.storage_cap_gb * 1e9:
            raise HTTPException(507, "armazenamento cheio: exclua projetos antigos antes de enviar")
        project = db.insert("projects", {
            "user_id": user.id, "title": body.title, "source_name": _safe_name(body.filename),
            "declared_duration": body.duration, "status": "awaiting_upload",
        })
        key = f"{project_prefix(user.id, project['id'])}/source/{_safe_name(body.filename)}"
        project = db.update("projects", project["id"], {"source_key": key})
        url = storage.presign_put(key, body.content_type, expires=6 * 3600)
        return {"project": project, "upload_url": url}

    @app.post("/api/projects/{project_id}/uploaded")
    def uploaded(project_id: str, user: User = Depends(current_user)):
        p = own("projects", project_id, user)
        h = storage.head(p["source_key"])
        if not h:
            raise HTTPException(409, "upload não encontrado no armazenamento")
        return db.update("projects", project_id, {"status": "uploaded", "source_size": h["size"]})

    @app.post("/api/projects/{project_id}/estimate")
    def estimate(project_id: str, req: JobRequest, user: User = Depends(current_user)):
        validate_opts(req)
        p = own("projects", project_id, user)
        est = estimate_for(p, req)
        state = budget_state()
        return {"estimate": est.to_dict(settings.usd_brl), "budget": state,
                "allowed": state["spent_usd"] + est.total_usd <= cap_usd}

    @app.post("/api/projects/{project_id}/jobs")
    def create_job(project_id: str, req: JobRequest, user: User = Depends(current_user)):
        validate_opts(req)
        p = own("projects", project_id, user)
        if p["status"] not in ("uploaded", "ready", "failed"):
            raise HTTPException(409, f"projeto em estado '{p['status']}'")
        return dispatch(user, p, req, "edit", None, None)

    @app.post("/api/jobs/{job_id}/revise")
    def revise(job_id: str, body: ReviseRequest, user: User = Depends(current_user)):
        parent = own("jobs", job_id, user)
        if parent["status"] != "done":
            raise HTTPException(409, "só é possível revisar um job concluído")
        p = own("projects", parent["project_id"], user)
        base = JobOptions.from_dict(parent.get("options") or {})
        req = body.options or JobRequest(**{k: v for k, v in base.__dict__.items() if k in JobRequest.model_fields})
        validate_opts(req)
        return dispatch(user, p, req, "revise", parent, body.instructions)

    @app.get("/api/renders/{render_id}/url")
    def render_url(render_id: str, download: bool = False, captions: bool = False, user: User = Depends(current_user)):
        r = own("renders", render_id, user)
        key = r["captions_key"] if captions else r["r2_key"]
        if not key:
            raise HTTPException(404, "arquivo indisponível")
        name = None
        if download or captions:
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", r.get("title") or "video")[:60] or "video"
            name = f"{stem}.{'srt' if captions else 'mp4'}"
        return {"url": storage.presign_get(key, expires=3600, filename=name)}

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, user: User = Depends(current_user)):
        p = own("projects", project_id, user)
        n = storage.delete_prefix(project_prefix(user.id, p["id"]) + "/")
        db.update("projects", project_id, {"status": "archived"})
        return {"deleted_objects": n}

    return app
