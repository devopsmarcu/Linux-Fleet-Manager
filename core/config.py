from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Carrega configuração a partir de um arquivo YAML (config.yaml)."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        if yaml_path.is_file():
            with yaml_path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    self._data = {str(k).lower(): v for k, v in loaded.items()}

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._data.get(field_name.lower()), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._data


class Settings(BaseSettings):
    """Centralized configuration for Linux Fleet Manager."""

    model_config = SettingsConfigDict(
        env_prefix="LFM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        base_dir = Path(__file__).resolve().parent.parent
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, base_dir / "config.yaml"),
            file_secret_settings,
        )

    project_name: str = "Linux Fleet Manager"
    version: str = "0.1.0"

    @staticmethod
    def _resolve(suffix: str = "") -> Path:
        return Path(__file__).resolve().parent.parent / suffix

    base_dir: Path = Field(default_factory=lambda: Settings._resolve())
    inventory_dir: Path = Field(default_factory=lambda: Settings._resolve("inventory"))
    logs_dir: Path = Field(default_factory=lambda: Settings._resolve("logs"))
    reports_dir: Path = Field(default_factory=lambda: Settings._resolve("reports"))
    ansible_dir: Path = Field(default_factory=lambda: Settings._resolve("ansible"))
    scripts_dir: Path = Field(default_factory=lambda: Settings._resolve("scripts"))

    log_level: Literal[
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    ] = "INFO"
    log_format: Literal["console", "json"] = "console"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 7

    ansible_inventory_file: str = "hosts.ini"
    ansible_forks: int = 10
    ansible_timeout: int = 30
    ansible_cfg_path: Path | None = None

    default_output_format: Literal["table", "json", "yaml"] = "table"

    def ensure_dirs(self) -> None:
        paths = (
            self.logs_dir,
            self.reports_dir,
            self.inventory_dir,
            self.ansible_dir,
            self.scripts_dir,
        )
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
