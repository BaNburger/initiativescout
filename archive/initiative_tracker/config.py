from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


def _resolve_project_root() -> Path:
    override = os.getenv("INITIATIVE_TRACKER_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd().resolve()


class Settings(BaseModel):
    project_root: Path = Field(default_factory=_resolve_project_root)
    data_dir: Path = Field(default_factory=lambda: _resolve_project_root() / "data")
    exports_dir: Path = Field(default_factory=lambda: _resolve_project_root() / "data" / "exports")
    reports_dir: Path = Field(default_factory=lambda: _resolve_project_root() / "reports" / "latest")
    config_dir: Path = Field(default_factory=lambda: _resolve_project_root() / "config")

    database_path: Path = Field(default_factory=lambda: _resolve_project_root() / "data" / "initiatives.db")

    user_agent: str = "UnicornInitiativeBot/1.0 (+https://unicorninitiative.local)"
    request_timeout_seconds: float = 15.0
    max_retries: int = 3
    request_backoff_seconds: float = 1.0
    directory_request_delay_seconds: float = 1.0
    website_request_delay_seconds: float = 0.25
    crawl_default_mode: str = "safe"
    crawl_max_pages_default: int = 12

    tum_directory_url: str = "https://www.tum.de/community/campusleben/student-clubs-galerie"
    lmu_directory_url: str = "https://www.lmu.de/de/workspace-fuer-studierende/studieren-und-leben/studentische-initiativen"
    hm_directory_url: str = "https://www.hm.edu/studium_1/im_studium/rund_ums_studium/aktivitaeten"

    seed_markdown_files: list[Path] = Field(
        default_factory=lambda: [
            _resolve_project_root() / "docs" / "research" / "munich_student_initiatives_comprehensive.md",
            _resolve_project_root() / "docs" / "research" / "munich_student_initiatives_database.md",
        ]
    )

    technology_taxonomy_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "technology_taxonomy.yaml"
    )
    market_taxonomy_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "market_taxonomy.yaml"
    )
    technology_aliases_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "technology_aliases.yaml"
    )
    market_aliases_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "market_aliases.yaml"
    )
    llm_scoring_config_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "llm_scoring_config.yaml"
    )
    tier_thresholds_file: Path = Field(
        default_factory=lambda: _resolve_project_root() / "config" / "tier_thresholds.yaml"
    )

    social_domains_to_skip: set[str] = Field(
        default_factory=lambda: {
            "linkedin.com",
            "www.linkedin.com",
            "instagram.com",
            "www.instagram.com",
            "facebook.com",
            "www.facebook.com",
            "x.com",
            "twitter.com",
            "youtube.com",
            "www.youtube.com",
            "tiktok.com",
            "www.tiktok.com",
        }
    )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def load_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data

    def load_technology_taxonomy(self) -> dict[str, list[str]]:
        raw = self.load_yaml(self.technology_taxonomy_file)
        taxonomy = raw.get("technology_domains", {})
        return {k: list(v) for k, v in taxonomy.items() if isinstance(v, list)}

    def load_market_taxonomy(self) -> dict[str, list[str]]:
        raw = self.load_yaml(self.market_taxonomy_file)
        taxonomy = raw.get("market_domains", {})
        return {k: list(v) for k, v in taxonomy.items() if isinstance(v, list)}

    def load_technology_aliases(self) -> dict[str, str]:
        raw = self.load_yaml(self.technology_aliases_file)
        payload = raw.get("aliases", {})
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            out[key.casefold()] = value
        return out

    def load_market_aliases(self) -> dict[str, str]:
        raw = self.load_yaml(self.market_aliases_file)
        payload = raw.get("aliases", {})
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            out[key.casefold()] = value
        return out

    def load_llm_scoring_config(self) -> dict[str, Any]:
        return self.load_yaml(self.llm_scoring_config_file)

    def load_tier_thresholds(self) -> dict[str, Any]:
        return self.load_yaml(self.tier_thresholds_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
