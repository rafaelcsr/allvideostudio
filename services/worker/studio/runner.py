"""Cloud job runner: R2 in, process(), R2 out, Supabase status + cost ledger."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from studio.budget import RENDER_CORES, cpu_cost, gpu_cost
from studio.config import Settings
from studio.db import DB, record_usage
from studio.models import Analysis, Transcript
from studio.pipeline.process import JobOptions, process
from studio.storage import Storage

log = logging.getLogger(__name__)


def project_prefix(user_id: str, project_id: str) -> str:
    return f"users/{user_id}/projects/{project_id}"


def run_job(
    job_id: str,
    db: DB,
    storage: Storage,
    transcribe_fn: Callable[[Path], tuple[Transcript, float]],
    settings: Settings,
) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise ValueError(f"job {job_id} não encontrado")
    if job["status"] not in ("queued", "failed"):
        return {"skipped": job["status"]}
    project = db.get("projects", job["project_id"])
    uid, pid = project["user_id"], project["id"]
    prefix = project_prefix(uid, pid)
    db.update("jobs", job_id, {"status": "running", "progress": 0, "stage": "iniciando", "error": None})
    db.update("projects", pid, {"status": "processing"})

    last = {"t": 0.0, "stage": ""}

    def progress(p: float, stage: str) -> None:
        now = time.time()
        if now - last["t"] > 2.0 or stage.split(":")[0] != last["stage"].split(":")[0] or p >= 1.0:
            last.update(t=now, stage=stage)
            try:
                db.update("jobs", job_id, {"progress": round(min(1.0, p), 3), "stage": stage[:120]})
            except Exception:  # noqa: BLE001 — progress is best effort
                log.warning("progress update failed", exc_info=True)

    tmp = Path(tempfile.mkdtemp(prefix=f"job_{job_id[:8]}_"))
    t0 = time.time()
    try:
        progress(0.01, "baixando original")
        src = tmp / ("source" + Path(project.get("source_name") or "v.mp4").suffix.lower())
        storage.download_file(project["source_key"], src)
        head = storage.head(project["source_key"]) or {}

        # cached transcript (same source etag) — never pay transcription twice
        transcript = None
        tkey = f"{prefix}/derived/transcript.json"
        cached = storage.get_bytes(tkey)
        if cached:
            data = json.loads(cached)
            if data.get("etag") == head.get("etag"):
                transcript = Transcript.from_dict(data["transcript"])

        previous = None
        if job.get("parent_job_id"):
            parent = db.get("jobs", job["parent_job_id"])
            if parent and parent.get("analysis"):
                previous = Analysis.from_dict(parent["analysis"])
        elif not (job.get("options") or {}).get("reanalyze"):
            # reuse the most recent analysis of this project (same transcript)
            prev_jobs = db.select("jobs", eq={"project_id": pid, "status": "done"}, order="created_at.desc", limit=1)
            if prev_jobs and prev_jobs[0].get("analysis") and transcript is not None:
                previous = Analysis.from_dict(prev_jobs[0]["analysis"])

        opts = JobOptions.from_dict(job.get("options") or {})
        opts.max_llm_usd = settings.max_llm_usd_per_job
        opts.shorts = max(0, min(opts.shorts, settings.max_shorts_per_job))

        res = process(src, tmp / "out", opts, transcribe_fn, transcript=transcript,
                      previous_analysis=previous, progress=progress, max_minutes=settings.max_video_minutes)

        if transcript is None:
            storage.put_bytes(tkey, json.dumps({"etag": head.get("etag"), "transcript": res.transcript.to_dict()},
                                               ensure_ascii=False).encode(), "application/json")

        progress(0.99, "enviando resultados")
        out_bytes = 0
        renders = []
        for o in res.outputs:
            base = f"{prefix}/renders/{job_id}/{o.path.stem}"
            storage.upload_file(o.path, base + ".mp4", "video/mp4")
            out_bytes += o.path.stat().st_size
            srt_key = None
            if o.srt_path:
                srt_key = base + ".srt"
                storage.upload_file(o.srt_path, srt_key, "application/x-subrip")
            renders.append(db.insert("renders", {
                "job_id": job_id, "project_id": pid, "user_id": uid, "kind": o.kind, "variant": o.variant,
                "title": o.title[:200], "r2_key": base + ".mp4", "captions_key": srt_key, "aspect": o.aspect,
                "duration": o.duration, "width": o.width, "height": o.height,
                "size_bytes": o.path.stat().st_size, "qc": o.qc,
            }))

        wall = time.time() - t0
        cost = 0.0
        if res.gpu_seconds:
            c = gpu_cost(res.gpu_seconds)
            record_usage(db, uid, job_id, "modal_gpu", res.gpu_seconds, "s", c, "transcrição faster-whisper")
            cost += c
        if res.llm_usage:
            c = float(res.llm_usage.get("cost_usd", 0))
            record_usage(db, uid, job_id, "anthropic", res.llm_usage.get("input_tokens", 0) + res.llm_usage.get("output_tokens", 0),
                         "tokens", c, f"análise editorial ({res.llm_usage.get('model')})")
            cost += c
        c = cpu_cost(wall)
        record_usage(db, uid, job_id, "modal_cpu", wall * RENDER_CORES, "core_s", c, "render/QC")
        cost += c
        c = out_bytes / 1e9 * 0.015
        record_usage(db, uid, job_id, "r2_storage", out_bytes / 1e9, "GB-mês", c, "saídas")
        cost += c

        db.update("jobs", job_id, {
            "status": "done", "progress": 1, "stage": "concluído", "cost_usd": round(cost, 4),
            "analysis": res.analysis.to_dict(), "style": res.style, "transcript_key": tkey,
            "warnings": res.warnings, "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        db.update("projects", pid, {"status": "ready", "duration": res.transcript.duration})
        return {"job_id": job_id, "renders": len(renders), "cost_usd": cost, "warnings": res.warnings}
    except Exception as e:  # noqa: BLE001
        log.error("job %s failed: %s", job_id, traceback.format_exc())
        wall = time.time() - t0
        try:
            record_usage(db, uid, job_id, "modal_cpu", wall * RENDER_CORES, "core_s", cpu_cost(wall), "job com falha")
        finally:
            db.update("jobs", job_id, {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:500]}", "stage": "falhou"})
            ready = db.select("jobs", eq={"project_id": pid, "status": "done"}, limit=1)
            db.update("projects", pid, {"status": "ready" if ready else "failed"})
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
