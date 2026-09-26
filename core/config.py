from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized configuration for Linux Fleet Manager."""

    model_config = SettingsConfigDict(
        env_prefix="LFM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        yaml_file="config.yaml",
        extra="ignore",
    )

    project_name: str = "Linux Fleet Manager"
    version: str = "0.1.0"

    base_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    inventory_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent / "inventory")
    logs_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent / "logs")
    reports_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent / "reports")
    ansible_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent / "ansible")
    scripts_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent / "scripts")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 7

    ansible_inventory_file: str = "hosts.yaml"
    ansible_forks: int = 10
    ansible_timeout: int = 30
    ansible_cfg_path: Path | None = None

    default_output_format: Literal["table", "json", "yaml"] = "table"

    def ensure_dirs(self) -> None:
        for path in (self.logs_dir, self.reports_dir, self.inventory_dir, self.ansible_dir, self.scripts_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
