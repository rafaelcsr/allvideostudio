"""Modal deployment: everything scales to zero — nothing runs (or bills) while idle.

  api            FastAPI (CPU, scale-to-zero)            -> https://<workspace>--ai-video-studio-api.modal.run
  process_job    render pipeline (4 CPU / 8 GiB)          spawned per job, max 2 concurrent
  transcribe     faster-whisper large-v3-turbo on 1x L4   called by process_job, max 1 concurrent
  housekeeping   daily cron: stale jobs, storage report

Deploy:  cd services/worker && modal deploy modal_app.py
Secrets: modal secret create ai-video-studio --from-dotenv ../../.env   (see docs/SETUP.md)
"""
from __future__ import annotations

import modal

APP_NAME = "ai-video-studio"
app = modal.App(APP_NAME)
secrets = [modal.Secret.from_name("ai-video-studio")]

FONT_PKGS = ["fonts-inter", "fonts-lato", "fonts-montserrat", "fonts-dejavu-core", "fonts-liberation"]
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

base_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "fontconfig", "wget")
    .run_commands(*[f"apt-get install -y {p} || true" for p in FONT_PKGS], "fc-cache -f")
    .run_commands(f"mkdir -p /opt/models && (wget -q -O /opt/models/yunet.onnx {YUNET_URL} || true)")
    .pip_install(
        "numpy>=1.26", "opencv-python-headless>=4.9,<5", "httpx>=0.27", "boto3>=1.34",
        "anthropic>=0.70", "fastapi>=0.115", "pydantic>=2.7",
    )
    .env({"YUNET_MODEL": "/opt/models/yunet.onnx", "PYTHONUNBUFFERED": "1"})
    .add_local_python_source("studio")
)

gpu_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .pip_install("faster-whisper>=1.1", "numpy>=1.26")
    .env({"WHISPER_CACHE": "/models", "WHISPER_DEVICE": "cuda", "WHISPER_COMPUTE": "float16"})
    .add_local_python_source("studio")
)

models_volume = modal.Volume.from_name("ai-video-studio-whisper", create_if_missing=True)


@app.function(image=gpu_image, gpu="L4", volumes={"/models": models_volume}, timeout=1800,
              max_containers=1, scaledown_window=30, secrets=secrets)
def transcribe(audio: bytes, suffix: str = ".flac") -> tuple[dict, float]:
    import tempfile
    import time

    from studio.pipeline.transcribe import transcribe as run

    t0 = time.time()
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(audio)
        f.flush()
        t = run(f.name)
    models_volume.commit()
    return t.to_dict(), time.time() - t0


@app.function(image=base_image, cpu=4.0, memory=8192, timeout=3600, max_containers=2, retries=0, secrets=secrets)
def process_job(job_id: str) -> dict:
    import logging
    from pathlib import Path

    from studio.config import Settings
    from studio.db import SupabaseDB
    from studio.models import Transcript
    from studio.runner import run_job
    from studio.storage import R2Storage

    logging.basicConfig(level=logging.INFO)
    s = Settings()

    def remote_transcribe(audio_path: Path):
        data, secs = transcribe.remote(audio_path.read_bytes(), audio_path.suffix)
        return Transcript.from_dict(data), secs

    return run_job(job_id, SupabaseDB(s), R2Storage(s), remote_transcribe, s)


@app.function(image=base_image, secrets=secrets, schedule=modal.Cron("0 6 * * *"), timeout=600)
def housekeeping() -> dict:
    """Fail stale jobs, report storage, record an alert when near the caps."""
    import datetime as dt

    from studio.config import Settings
    from studio.db import SupabaseDB, month_spend_usd, month_start
    from studio.storage import R2Storage

    s = Settings()
    db, st = SupabaseDB(s), R2Storage(s)
    limit = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    stale = 0
    for status in ("queued", "running"):
        for j in db.select("jobs", eq={"status": status}):
            if j.get("updated_at", j["created_at"]) < limit:
                db.update("jobs", j["id"], {"status": "failed", "error": "tempo limite excedido (housekeeping)"})
                stale += 1
    used_gb = st.total_bytes() / 1e9
    spent = month_spend_usd(db)
    alerts = []
    if used_gb > s.storage_cap_gb * 0.85:
        alerts.append(f"Armazenamento em {used_gb:.1f} GB (teto {s.storage_cap_gb} GB)")
    if spent > s.monthly_budget_usd * s.budget_alert_pct:
        alerts.append(f"Gasto do mês US$ {spent:.2f} de US$ {s.monthly_budget_usd:.2f}")
    for a in alerts:
        db.insert("budget_alerts", {"month": month_start()[:10], "level": "warning", "message": a})
    return {"stale_jobs": stale, "storage_gb": used_gb, "spent_usd": spent, "alerts": alerts}


@app.function(image=base_image, secrets=secrets, scaledown_window=120, timeout=60)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from studio.api import create_app
    from studio.config import Settings
    from studio.db import SupabaseDB
    from studio.storage import R2Storage

    s = Settings()

    def spawn(job_id: str) -> str:
        return process_job.spawn(job_id).object_id

    return create_app(s, SupabaseDB(s), R2Storage(s), spawn)
