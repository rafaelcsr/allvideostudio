"""Runner end-to-end with in-memory DB + local storage (cloud flow without the cloud)."""
from conftest import make_transcript, needs_ffmpeg, speak

from studio.config import Settings
from studio.db import MemoryDB
from studio.runner import run_job
from studio.storage import LocalStorage


@needs_ffmpeg
def test_run_job_uploads_renders_and_ledger(synthetic_video, tmp_path):
    db, st = MemoryDB(), LocalStorage(tmp_path / "r2")
    p = db.insert("projects", {"user_id": "u1", "title": "t", "source_name": "src.mp4", "status": "uploaded"})
    key = f"users/u1/projects/{p['id']}/source/src.mp4"
    db.update("projects", p["id"], {"source_key": key})
    st.upload_file(synthetic_video, key)
    job = db.insert("jobs", {"project_id": p["id"], "user_id": "u1", "status": "queued", "kind": "edit",
                             "options": {"style": "corporativo", "shorts": 0, "use_llm": False}})
    t = make_transcript(speak("olá a todos.", start=0.3) + speak("seguimos aqui.", start=5.2), duration=12)
    calls = []

    def fake(audio):
        calls.append(audio)
        return t, 3.0

    res = run_job(job["id"], db, st, fake, Settings())
    j = db.get("jobs", job["id"])
    assert j["status"] == "done", j.get("error")
    assert res["renders"] == 1
    r = db.select("renders")[0]
    assert st.head(r["r2_key"]) and r["qc"]["ok"]
    services = {u["service"] for u in db.select("usage_ledger")}
    assert {"modal_gpu", "modal_cpu", "r2_storage"} <= services

    # second job reuses cached transcript (no transcription call)
    job2 = db.insert("jobs", {"project_id": p["id"], "user_id": "u1", "status": "queued", "kind": "edit",
                              "options": {"style": "cinematografico", "shorts": 0, "use_llm": False}})
    run_job(job2["id"], db, st, fake, Settings())
    assert len(calls) == 1
    assert db.get("jobs", job2["id"])["status"] == "done"
