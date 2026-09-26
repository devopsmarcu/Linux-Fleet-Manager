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
    def test_lfm_error_base_message(self) -> None:
        err = LFMError("algo falhou")
        assert str(err) == "algo falhou"
        assert err.message == "algo falhou"
        assert err.details == {}

    def test_lfm_error_with_details(self) -> None:
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
    def test_exception_inheritance(self, exc_cls: type[LFMError], parent: type[LFMError]) -> None:
        assert issubclass(exc_cls, parent)
        err = exc_cls("x", foo="bar")
        assert isinstance(err, LFMError)
        assert err.details["foo"] == "bar"


class TestSettingsDefaults:
    def test_default_values(self, tmp_project: Path) -> None:
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

    def test_ensure_dirs_creates_paths(self, tmp_project: Path) -> None:
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

    def test_get_settings_singleton(self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LFM_PROJECT_NAME", "Overridden")
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        assert isinstance(s1.base_dir, Path)


class TestSystemInfoParser:
    def test_normalize_host_info(self) -> None:
        from core.inventory import SystemInfoCollector
        from core.models import Host, HostStatusEnum

        host_obj = Host(name="ubuntu-01", address="192.168.1.10")
        raw_facts = {
            "hostname": "ubuntu-node-01",
            "ip": "192.168.1.10",
            "distribution": "Ubuntu",
            "distribution_version": "22.04",
            "kernel": "5.15.0-88-generic",
            "architecture": "x86_64",
            "cpu_model": "AMD EPYC",
            "cpu_cores": 4,
            "memory_total_mb": 8192,
            "mounts": [
                {
                    "mount": "/",
                    "size_total": 107374182400,
                    "size_available": 53687091200,
                }
            ],
            "uptime_seconds": 3660,
            "remote_user": "ubuntu",
            "python_version": "3.10.12",
            "pending_updates": 3,
            "services": ["sshd", "docker"],
        }

        info = SystemInfoCollector.normalize_host_info("ubuntu-01", host_obj, raw_facts)
        assert info.host == "ubuntu-01"
        assert info.status == HostStatusEnum.ONLINE
        assert info.hostname == "ubuntu-node-01"
        assert info.ip == "192.168.1.10"
        assert info.os_display == "Ubuntu 22.04"
        assert info.kernel == "5.15.0-88-generic"
        assert info.architecture == "x86_64"
        assert info.cpu_cores == 4
        assert info.ram_display == "8.0 GB"
        assert info.disk_total_gb == 100.0
        assert info.disk_used_gb == 50.0
        assert info.disk_display == "50.0 / 100.0 GB"
        assert info.uptime_display == "1h 1m"
        assert info.pending_updates == 3
        assert "sshd" in info.services

    def test_normalize_offline_host(self) -> None:
        from core.models import HostStatusEnum, HostSystemInfo

        info = HostSystemInfo(
            host="debian-01",
            address="192.168.1.20",
            status=HostStatusEnum.OFFLINE,
            error_message="Connection timed out",
        )
        assert info.status == HostStatusEnum.OFFLINE
        assert info.os_display == "N/A"
        assert info.ram_display == "N/A"
        assert info.disk_display == "N/A"
        assert info.uptime_display == "N/A"

