from __future__ import annotations

from pathlib import Path

import pytest

from core.config import Settings, get_settings
from core.exceptions import (
    AnsibleAuthError,
    AnsibleError,
    AnsibleTimeoutError,
    AnsibleUnreachableError,
    CommandExecutionError,
    ConfigError,
    InventoryError,
    LFMError,
    ReportError,
)


class TestExceptionsHierarchy:
    def test_lfm_error_base_message(self):
        err = LFMError("algo falhou")
        assert str(err) == "algo falhou"
        assert err.message == "algo falhou"
        assert err.details == {}

    def test_lfm_error_with_details(self):
        err = LFMError("operação falhou", host="web-01", code=5)
        assert "host=web-01" in str(err)
        assert "code=5" in str(err)
        assert err.details["host"] == "web-01"

    @pytest.mark.parametrize(
        "exc_cls, parent",
        [
            (ConfigError, LFMError),
            (InventoryError, LFMError),
            (AnsibleError, LFMError),
            (AnsibleUnreachableError, AnsibleError),
            (AnsibleAuthError, AnsibleError),
            (AnsibleTimeoutError, AnsibleError),
            (CommandExecutionError, LFMError),
            (ReportError, LFMError),
        ],
    )
    def test_exception_inheritance(self, exc_cls, parent):
        assert issubclass(exc_cls, parent)
        err = exc_cls("x", foo="bar")
        assert isinstance(err, LFMError)
        assert err.details["foo"] == "bar"


class TestSettingsDefaults:
    def test_default_values(self, tmp_project: Path):
        s = Settings(
            base_dir=tmp_project,
            logs_dir=tmp_project / "logs",
            reports_dir=tmp_project / "reports",
            inventory_dir=tmp_project / "inventory",
            ansible_dir=tmp_project / "ansible",
            scripts_dir=tmp_project / "scripts",
        )
        assert s.project_name == "Linux Fleet Manager"
        assert s.version == "0.1.0"
        assert s.ansible_forks == 10
        assert s.ansible_timeout == 30
        assert s.log_level == "INFO"
        assert s.default_output_format == "table"

    def test_ensure_dirs_creates_paths(self, tmp_project: Path):
        s = Settings(
            base_dir=tmp_project,
            logs_dir=tmp_project / "logs-new",
            reports_dir=tmp_project / "reports-new",
            inventory_dir=tmp_project / "inv-new",
            ansible_dir=tmp_project / "ans-new",
            scripts_dir=tmp_project / "scr-new",
        )
        for p in (s.logs_dir, s.reports_dir, s.inventory_dir, s.ansible_dir, s.scripts_dir):
            assert not p.exists()
        s.ensure_dirs()
        for p in (s.logs_dir, s.reports_dir, s.inventory_dir, s.ansible_dir, s.scripts_dir):
            assert p.is_dir()

    def test_get_settings_singleton(self, tmp_project: Path, monkeypatch):
        monkeypatch.setenv("LFM_PROJECT_NAME", "Overridden")
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        assert isinstance(s1.base_dir, Path)
