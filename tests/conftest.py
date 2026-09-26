from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    base = tmp_path / "lfm-project"
    for d in ("logs", "reports", "inventory", "ansible", "scripts"):
        (base / d).mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture
def settings(tmp_project: Path) -> Settings:
    s = Settings(
        base_dir=tmp_project,
        logs_dir=tmp_project / "logs",
        reports_dir=tmp_project / "reports",
        inventory_dir=tmp_project / "inventory",
        ansible_dir=tmp_project / "ansible",
        scripts_dir=tmp_project / "scripts",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def quiet_logger() -> logging.Logger:
    logger = logging.getLogger("lfm")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL + 1)
    yield logger
    logger.setLevel(previous_level)
