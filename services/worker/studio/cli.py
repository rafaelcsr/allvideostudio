"""Offline CLI — same pipeline as the cloud, for development and debugging.

  python -m studio.cli edit video.mp4 --out saida/ --style dinamico --shorts 2 [--no-llm]
  python -m studio.cli estimate --minutes 10 --shorts 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from studio.budget import estimate_job
from studio.config import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit")
    e.add_argument("video")
    e.add_argument("--out", default="saida")
    e.add_argument("--style", default="auto")
    e.add_argument("--shorts", type=int, default=2)
    e.add_argument("--aspect", default="16:9")
    e.add_argument("--hook-open", action="store_true")
    e.add_argument("--no-llm", action="store_true")
    e.add_argument("--whisper-model", default=None)
    es = sub.add_parser("estimate")
    es.add_argument("--minutes", type=float, required=True)
    es.add_argument("--shorts", type=int, default=3)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "estimate":
        s = Settings()
        est = estimate_job(args.minutes * 60, shorts=args.shorts)
        print(json.dumps(est.to_dict(s.usd_brl), indent=2))
        return 0

    from studio.models import Transcript
    from studio.pipeline.process import JobOptions, process
    from studio.pipeline.transcribe import transcribe

    out = Path(args.out)
    cache = out / "transcript.json"

    def local_transcribe(audio: Path):
        t0 = time.time()
        t = transcribe(audio, model_name=args.whisper_model)
        cache.write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")
        return t, time.time() - t0

    transcript = Transcript.from_dict(json.loads(cache.read_text(encoding="utf-8"))) if cache.exists() else None
    opts = JobOptions(style=args.style, shorts=args.shorts, full_aspect=args.aspect,
                      hook_open=args.hook_open, use_llm=not args.no_llm)

    def progress(p: float, s: str) -> None:
        print(f"[{p * 100:5.1f}%] {s}", file=sys.stderr)

    res = process(args.video, out, opts, local_transcribe, transcript=transcript, progress=progress, max_minutes=240)
    (out / "analysis.json").write_text(json.dumps(res.analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    for o in res.outputs:
        print(f"{o.kind:5s} {o.path}  {o.width}x{o.height} {o.duration:.1f}s  QC={'OK' if o.qc['ok'] else 'FALHOU'}")
    for w in res.warnings:
        print("aviso:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
