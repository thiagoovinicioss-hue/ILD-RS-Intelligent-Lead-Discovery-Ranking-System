"""Application configuration.

All settings are read from environment variables (or a ``.env`` file).
Secrets are never stored in code and never serialized through the API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

FEATURE_KEYS = (
    "web_presence",
    "rating_score",
    "review_volume",
    "business_status",
    "contact_availability",
    "category_fit",
    "location_fit",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "web_presence": 0.20,
    "rating_score": 0.15,
    "review_volume": 0.15,
    "business_status": 0.10,
    "contact_availability": 0.15,
    "category_fit": 0.15,
    "location_fit": 0.10,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="ILD_",
        extra="ignore",
    )

    # Persistence ---------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./ildrs.db"

    # Source adapter ------------------------------------------------------
    source: str = "fixture"
    google_places_api_key: str = ""
    google_places_region: str = ""
    google_places_language: str = ""

    # Discovery -----------------------------------------------------------
    discovery_query: str = "plumbing services"
    discovery_location: str = ""
    discovery_radius_m: int = 20000
    discovery_limit: int = 50
    discovery_categories: str = "plumber,contractor"

    # Rating --------------------------------------------------------------
    rating_model: str = "v1"
    rating_min_samples: int = 20

    # Feature weights -----------------------------------------------------
    weight_web_presence: float = 0.20
    weight_rating_score: float = 0.15
    weight_review_volume: float = 0.15
    weight_business_status: float = 0.10
    weight_contact_avail: float = 0.15
    weight_category_fit: float = 0.15
    weight_location_fit: float = 0.10

    # Scheduling ----------------------------------------------------------
    verify_interval_hours: float = 24.0
    refresh_interval_hours: float = 6.0

    # Notifications -------------------------------------------------------
    notify_webhook_url: str = ""

    # API -----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    # Observability -------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("source")
    @classmethod
    def _source_supported(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"fixture", "google_places"}:
            raise ValueError(f"unsupported source '{value}' (fixture | google_places)")
        return value

    @field_validator("rating_model")
    @classmethod
    def _model_supported(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"v1", "v2", "v3", "v4"}:
            raise ValueError(f"unsupported model '{value}' (v1|v2|v3|v4)")
        return value

    @field_validator("discovery_location")
    @classmethod
    def _validate_location(cls, value: str) -> str:
        value = value.strip()
        if value and not _is_lat_lng(value):
            raise ValueError(f"ILD_DISCOVERY_LOCATION must be 'lat,lng', got '{value}'")
        return value

    # -- helpers ----------------------------------------------------------

    @property
    def feature_weights(self) -> dict[str, float]:
        """Effective per-feature weights (from env overrides)."""
        mapping = {
            "web_presence": self.weight_web_presence,
            "rating_score": self.weight_rating_score,
            "review_volume": self.weight_review_volume,
            "business_status": self.weight_business_status,
            "contact_availability": self.weight_contact_avail,
            "category_fit": self.weight_category_fit,
            "location_fit": self.weight_location_fit,
        }
        return {k: mapping[k] for k in FEATURE_KEYS}

    @property
    def target_categories(self) -> list[str]:
        return [c.strip().lower() for c in self.discovery_categories.split(",") if c.strip()]

    @property
    def discovery_location_coords(self) -> tuple[float, float] | None:
        if not self.discovery_location:
            return None
        lat, lng = self.discovery_location.split(",")
        return float(lat.strip()), float(lng.strip())

    def public_dict(self) -> dict[str, Any]:
        """Non-secret configuration for API/CLI display."""
        from ildrs import __version__

        return {
            "version": __version__,
            "source": self.source,
            "google_places_enabled": bool(self.google_places_api_key),
            "discovery_query": self.discovery_query,
            "discovery_location": self.discovery_location or None,
            "discovery_radius_m": self.discovery_radius_m,
            "discovery_limit": self.discovery_limit,
            "discovery_categories": self.target_categories,
            "rating_model": self.rating_model,
            "rating_min_samples": self.rating_min_samples,
            "feature_weights": self.feature_weights,
            "verify_interval_hours": self.verify_interval_hours,
            "refresh_interval_hours": self.refresh_interval_hours,
            "notify_webhook_enabled": bool(self.notify_webhook_url),
            "api_host": self.api_host,
            "api_port": self.api_port,
            "log_level": self.log_level,
        }


def _is_lat_lng(value: str) -> bool:
    parts = value.split(",")
    if len(parts) != 2:
        return False
    try:
        lat, lng = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180


def get_settings() -> Settings:
    """Cached settings instance."""
    return _settings


_settings = Settings()
