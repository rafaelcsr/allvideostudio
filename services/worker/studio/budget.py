"""Cost estimation and hard budget enforcement.

Every job is estimated *before* it is queued; the API refuses jobs that would push
the month's spend past the cap. Actual usage is written to the ledger per service.
Rates are conservative list prices (no free credits assumed) and overridable by env."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from studio.pipeline.editorial import PRICES


def _r(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Rates:
    gpu_per_s: float = _r("RATE_GPU_L4_PER_S", 0.000222)  # Modal L4 ≈ $0.80/h
    cpu_core_per_s: float = _r("RATE_CPU_CORE_PER_S", 0.0000131)  # Modal ≈ $0.047/core-h
    mem_gib_per_s: float = _r("RATE_MEM_GIB_PER_S", 0.00000222)
    r2_gb_month: float = _r("RATE_R2_GB_MONTH", 0.015)


RATES = Rates()
RENDER_CORES = 4
RENDER_MEM_GIB = 8


class BudgetExceeded(Exception):
    pass


@dataclass
class Estimate:
    transcription_usd: float
    editorial_usd: float
    render_usd: float
    storage_usd: float

    @property
    def total_usd(self) -> float:
        return self.transcription_usd + self.editorial_usd + self.render_usd + self.storage_usd

    def to_dict(self, usd_brl: float) -> dict:
        d = {k: round(v, 4) for k, v in asdict(self).items()}
        d["total_usd"] = round(self.total_usd, 4)
        d["total_brl"] = round(self.total_usd * usd_brl, 2)
        return d


def cpu_cost(seconds: float, cores: int = RENDER_CORES, mem_gib: float = RENDER_MEM_GIB) -> float:
    return seconds * (cores * RATES.cpu_core_per_s + mem_gib * RATES.mem_gib_per_s)


def gpu_cost(seconds: float) -> float:
    return seconds * RATES.gpu_per_s


def estimate_job(
    duration_s: float,
    full: bool = True,
    shorts: int = 3,
    use_llm: bool = True,
    model: str = "claude-opus-5-5",
    transcript_cached: bool = False,
    revision: bool = False,
) -> Estimate:
    minutes = duration_s / 60
    transcribe = 0.0 if (transcript_cached or revision) else gpu_cost(25 + duration_s * 0.08)
    llm = 0.0
    if use_llm:
        words = duration_s * 2.6
        inp, out = words * 1.9 + 3500, 2500 + words * 0.35
        pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
        llm = (inp * pin + out * pout) / 1e6
        if revision:
            llm *= 1.25
    # render: clip pass + final pass, ~1.6x realtime CPU on 4 cores for 1080p; shorts ~60s 1080x1920
    render_s = 30 + (duration_s * 1.6 if full else 0) + shorts * 60 * 2.2 + duration_s * 0.15  # + face tracking/QC
    render = cpu_cost(render_s)
    out_gb = (minutes * 0.06 if full else 0) + shorts * 0.05
    storage = out_gb * RATES.r2_gb_month
    return Estimate(round(transcribe, 4), round(llm, 4), round(render, 4), round(storage, 4))


def check_budget(month_spent_usd: float, estimate_usd: float, cap_usd: float, alert_pct: float = 0.8) -> str | None:
    """Raise BudgetExceeded if the job would cross the cap; return an alert message near it."""
    projected = month_spent_usd + estimate_usd
    if projected > cap_usd:
        raise BudgetExceeded(
            f"Orçamento mensal insuficiente: gasto US$ {month_spent_usd:.2f} + estimativa US$ {estimate_usd:.2f} "
            f"> teto US$ {cap_usd:.2f}. Reduza o vídeo/saídas ou aumente o teto (MONTHLY_BUDGET_BRL)."
        )
    if projected >= cap_usd * alert_pct:
        return f"Atenção: {projected / cap_usd:.0%} do orçamento mensal será consumido após este job."
    return None
