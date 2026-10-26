"""
Zero UI — central configuration
===============================

Everything is driven by environment variables so that local development,
testing and deployment differ only by configuration.  Values are read from
``backend/.env`` when present (loaded once, here).

Development fallbacks exist so the prototype runs out-of-the-box (the
acceptance test must work with zero setup).  In ``ZEROUI_ENV=production`` the
application refuses to start without explicit tokens/secrets.
"""

from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

# Load backend/.env before anything reads os.environ
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# Development-only fallbacks (documented in README + .env.example)
_DEV_DASHBOARD_TOKEN = "zeroui-dev-dashboard-token"
_DEV_ENROLLMENT_TOKEN = "zeroui-dev-enroll-token"
_DEV_JWT_SECRET = "zeroui-dev-jwt-secret-change-me-at-least-32-bytes"


class Settings:
    """Runtime settings shared by the whole backend."""

    def __init__(self) -> None:
        self.env: str = _env("ZEROUI_ENV", "development").lower()
        self.is_production: bool = self.env == "production"

        # ── Paths ──
        self.base_dir: str = BASE_DIR
        self.project_dir: str = PROJECT_DIR
        self.data_dir: str = _env("ZEROUI_DATA_DIR", os.path.join(BASE_DIR, "data"))
        self.db_path: str = _env(
            "ZEROUI_DB_PATH", os.path.join(self.data_dir, "emergencies.db")
        )
        self.audio_dir: str = _env(
            "ZEROUI_AUDIO_DIR", os.path.join(self.data_dir, "audio")
        )
        self.mobile_simulator_dir: str = os.path.join(PROJECT_DIR, "mobile-simulator")
        self.dashboard_dir: str = os.path.join(PROJECT_DIR, "dashboard")

        # ── Auth ──
        self.dashboard_token: str = _env("ZEROUI_DASHBOARD_TOKEN", _DEV_DASHBOARD_TOKEN)
        self.enrollment_token: str = _env("ZEROUI_ENROLLMENT_TOKEN", _DEV_ENROLLMENT_TOKEN)
        self.jwt_secret: str = _env("ZEROUI_JWT_SECRET", _DEV_JWT_SECRET)
        self.dashboard_session_minutes: int = _env_int(
            "ZEROUI_DASHBOARD_SESSION_MINUTES", 12 * 60
        )

        # ── Emergency behaviour ──
        self.countdown_seconds: int = _env_int("ZEROUI_COUNTDOWN_SECONDS", 15)
        self.location_interval_seconds: int = _env_int(
            "ZEROUI_LOCATION_INTERVAL_SECONDS", 30
        )
        self.audio_segment_seconds: int = _env_int("ZEROUI_AUDIO_SEGMENT_SECONDS", 60)
        self.stale_after_seconds: int = _env_int("ZEROUI_STALE_AFTER_SECONDS", 90)
        self.max_audio_segment_bytes: int = _env_int(
            "ZEROUI_MAX_AUDIO_SEGMENT_BYTES", 25 * 1024 * 1024
        )
        self.max_live_frame_bytes: int = _env_int(
            "ZEROUI_MAX_LIVE_FRAME_BYTES", 64 * 1024
        )

        # ── CORS ──
        raw_origins = _env("ZEROUI_CORS_ORIGINS", "*")
        self.cors_origins: List[str] = (
            ["*"] if raw_origins == "*" else [o.strip() for o in raw_origins.split(",") if o.strip()]
        )

        # ── SMS (optional, Twilio) ──
        self.twilio_account_sid: str = _env("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token: str = _env("TWILIO_AUTH_TOKEN")
        self.twilio_phone_number: str = _env("TWILIO_PHONE_NUMBER")
        self.dashboard_public_url: str = _env("ZEROUI_DASHBOARD_URL")

        # ── Logging ──
        self.log_level: str = _env("ZEROUI_LOG_LEVEL", "INFO").upper()

        self.validate()

    # ------------------------------------------------------------------
    @property
    def dashboard_url_for_alerts(self) -> str:
        return self.dashboard_public_url or "http://localhost:8000/dashboard/"

    def validate(self) -> None:
        """Fail fast on dangerous production configurations."""
        if not self.is_production:
            return

        problems = []
        if self.dashboard_token == _DEV_DASHBOARD_TOKEN:
            problems.append("ZEROUI_DASHBOARD_TOKEN must be set in production")
        if self.enrollment_token == _DEV_ENROLLMENT_TOKEN:
            problems.append("ZEROUI_ENROLLMENT_TOKEN must be set in production")
        if self.jwt_secret == _DEV_JWT_SECRET:
            problems.append("ZEROUI_JWT_SECRET must be set in production")
        if "*" in self.cors_origins:
            problems.append("ZEROUI_CORS_ORIGINS must list explicit origins in production")

        if problems:
            raise RuntimeError(
                "Invalid production configuration:\n  - " + "\n  - ".join(problems)
            )

    def as_log_dict(self) -> dict:
        """Non-secret summary suitable for startup logging."""
        return {
            "env": self.env,
            "db_path": self.db_path,
            "audio_dir": self.audio_dir,
            "countdown_seconds": self.countdown_seconds,
            "location_interval_seconds": self.location_interval_seconds,
            "audio_segment_seconds": self.audio_segment_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "auth_enabled": True,
        }


settings = Settings()
