from __future__ import annotations

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


class TestCliGlobal:
    def test_help_shows_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "inventory" in result.stdout
        assert "status" in result.stdout
        assert "health" in result.stdout
        assert "version" in result.stdout

    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Linux Fleet Manager" in result.stdout
        assert "0.1.0" in result.stdout

    def test_version_command(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "Linux Fleet Manager" in result.stdout
        assert "0.1.0" in result.stdout
        assert "Diretório" in result.stdout

    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code in (0, 2)
        assert "Usage:" in result.stdout


class TestInventoryCommand:
    def test_list_default(self) -> None:
        result = runner.invoke(app, ["inventory", "list"])
        assert result.exit_code == 0
        assert "ubuntu-01" in result.stdout
        assert "debian-01" in result.stdout

    def test_list_with_group_filter(self) -> None:
        result = runner.invoke(app, ["inventory", "list", "--group", "ubuntu"])
        assert result.exit_code == 0
        assert "ubuntu-01" in result.stdout
        assert "debian-01" not in result.stdout

    def test_groups(self) -> None:
        result = runner.invoke(app, ["inventory", "groups"])
        assert result.exit_code == 0
        assert "ubuntu" in result.stdout
        assert "debian" in result.stdout

    def test_inventory_root_collect_json(self) -> None:
        result = runner.invoke(app, ["inventory", "--json", "--host", "localhost", "-t", "2"])
        assert result.exit_code == 0
        assert "localhost" in result.stdout
        assert '"host":' in result.stdout or '"status":' in result.stdout

    def test_inventory_root_specific_host(self) -> None:
        result = runner.invoke(app, ["inventory", "--host", "localhost", "-t", "2"])
        assert result.exit_code == 0
        assert "localhost" in result.stdout
        assert "HOST" in result.stdout
        assert "KERNEL" in result.stdout


class TestStatusCommand:
    def test_check_default(self, monkeypatch):
        # Mock AnsibleExecutor.ping to return a controlled list of results
        def mock_ping(self, targets="all", timeout=10):
            from core.models import Host, HostStatus, HostStatusEnum
            return [
                HostStatus(host=Host(name="ubuntu-01", address="1.1.1.1"), status=HostStatusEnum.ONLINE, latency_ms=10),
                HostStatus(host=Host(name="debian-01", address="1.1.1.2"), status=HostStatusEnum.OFFLINE),
            ]

        monkeypatch.setattr("ansible.runner.AnsibleExecutor.ping", mock_ping)

        result = runner.invoke(app, ["status", "check"])
        assert result.exit_code == 0
        assert "Linux Fleet Manager" in result.stdout
        assert "Total: 2" in result.stdout
        assert "Online: 1" in result.stdout
        assert "Offline: 1" in result.stdout

    def test_check_specific_host(self, monkeypatch):
        def mock_ping(self, targets="all", timeout=10):
            from core.models import Host, HostStatus, HostStatusEnum
            return [
                HostStatus(host=Host(name="ubuntu-01", address="1.1.1.1"), status=HostStatusEnum.ONLINE, latency_ms=10),
            ]

        monkeypatch.setattr("ansible.runner.AnsibleExecutor.ping", mock_ping)

        result = runner.invoke(app, ["status", "check", "--host", "ubuntu-01"])
        assert result.exit_code == 0
        assert "ubuntu-01" in result.stdout
        assert "debian-01" not in result.stdout


class TestHealthCommand:
    def test_run_default(self, monkeypatch):
        # Mock HealthChecker.run_health_check to return deterministic results
        def mock_run_health(self, targets="all", group=None, host_name=None, timeout=None, quick=False):
            from core.models import HostHealthReport, HealthState, CheckItemResult, Host
            return [
                HostHealthReport(
                    host="ubuntu-01",
                    address="1.1.1.1",
                    status=HealthState.HEALTHY,
                    checks=[
                        CheckItemResult(name="Connectivity", passed=True, state=HealthState.HEALTHY),
                        CheckItemResult(name="SSH", passed=True, state=HealthState.HEALTHY),
                        CheckItemResult(name="Disk", passed=True, state=HealthState.HEALTHY),
                        CheckItemResult(name="Memory", passed=True, state=HealthState.HEALTHY),
                        CheckItemResult(name="CPU Load", passed=True, state=HealthState.HEALTHY),
                    ]
                )
            ]

        monkeypatch.setattr("core.health.HealthChecker.run_health_check", mock_run_health)

        result = runner.invoke(app, ["health", "run"])
        assert result.exit_code == 0
        assert "Health Check" in result.stdout
        assert "Disk" in result.stdout
        assert "Connectivity" in result.stdout

    def test_run_quick(self) -> None:
        result = runner.invoke(app, ["health", "run", "--quick"])
        assert result.exit_code == 2
        assert "Health Check" in result.stdout
        assert "rápido" in result.stdout
        assert "LOAD" not in result.stdout
        assert "REBOOT" not in result.stdout
