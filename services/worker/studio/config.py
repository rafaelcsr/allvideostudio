"""Runtime configuration — everything comes from environment variables / secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(*names: str) -> str:
    """First non-empty variable among `names` (new name first, legacy aliases after)."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def supabase_headers(key: str) -> dict[str, str]:
    """Headers for Supabase REST/Auth calls made *as the project* (not as a user).

    New keys (sb_publishable_/sb_secret_) go only in `apikey`; legacy JWT keys
    (anon/service_role) must also be sent as the bearer token."""
    h = {"apikey": key}
    if key.startswith("eyJ"):
        h["Authorization"] = f"Bearer {key}"
    return h


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", "").rstrip("/"))
    # public key (safe for browsers): sb_publishable_… or legacy anon JWT
    supabase_anon_key: str = field(default_factory=lambda: _env("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"))
    # privileged key (server only, bypasses RLS): sb_secret_… or legacy service_role JWT
    supabase_service_key: str = field(default_factory=lambda: _env("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"))
    r2_account_id: str = field(default_factory=lambda: os.environ.get("R2_ACCOUNT_ID", ""))
    r2_access_key: str = field(default_factory=lambda: os.environ.get("R2_ACCESS_KEY_ID", ""))
    r2_secret_key: str = field(default_factory=lambda: os.environ.get("R2_SECRET_ACCESS_KEY", ""))
    r2_bucket: str = field(default_factory=lambda: os.environ.get("R2_BUCKET", "ai-video-studio"))
    allowed_emails: tuple[str, ...] = field(default_factory=lambda: tuple(
        e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
    ))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
    ))
    # --- financial guard rails ---
    monthly_budget_brl: float = field(default_factory=lambda: _f("MONTHLY_BUDGET_BRL", 100))
    usd_brl: float = field(default_factory=lambda: _f("USD_BRL", 5.5))
    budget_alert_pct: float = field(default_factory=lambda: _f("BUDGET_ALERT_PCT", 0.8))
    max_video_minutes: float = field(default_factory=lambda: _f("MAX_VIDEO_MINUTES", 30))
    max_upload_mb: float = field(default_factory=lambda: _f("MAX_UPLOAD_MB", 4000))
    max_jobs_per_day: int = field(default_factory=lambda: int(_f("MAX_JOBS_PER_DAY", 12)))
    max_llm_usd_per_job: float = field(default_factory=lambda: _f("MAX_LLM_USD_PER_JOB", 0.75))
    max_shorts_per_job: int = field(default_factory=lambda: int(_f("MAX_SHORTS_PER_JOB", 5)))
    storage_cap_gb: float = field(default_factory=lambda: _f("STORAGE_CAP_GB", 9.0))
    tmp_retention_days: int = field(default_factory=lambda: int(_f("TMP_RETENTION_DAYS", 2)))

    @property
    def monthly_budget_usd(self) -> float:
        return self.monthly_budget_brl / self.usd_brl

    @property
    def r2_endpoint(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    def missing(self) -> list[str]:
        req = {
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_PUBLISHABLE_KEY": self.supabase_anon_key,
            "SUPABASE_SECRET_KEY": self.supabase_service_key,
            "R2_ACCOUNT_ID": self.r2_account_id,
            "R2_ACCESS_KEY_ID": self.r2_access_key,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_key,
        }
        return [k for k, v in req.items() if not v]
