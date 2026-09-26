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
    def test_check_default(self) -> None:
        result = runner.invoke(app, ["status", "check"])
        assert result.exit_code == 0
        assert "Linux Fleet Manager" in result.stdout
        assert "Total:" in result.stdout

    def test_check_specific_host(self) -> None:
        result = runner.invoke(app, ["status", "check", "--host", "ubuntu-01"])
        assert result.exit_code == 0
        assert "ubuntu-01" in result.stdout
        assert "debian-01" not in result.stdout


class TestHealthCommand:
    def test_run_default(self) -> None:
        result = runner.invoke(app, ["health", "run"])
        assert result.exit_code == 0
        assert "Health Check" in result.stdout
        assert "DISK" in result.stdout
        assert "REBOOT" in result.stdout

    def test_run_quick(self) -> None:
        result = runner.invoke(app, ["health", "run", "--quick"])
        assert result.exit_code == 0
        assert "rápido" in result.stdout
        assert "LOAD" not in result.stdout
        assert "REBOOT" not in result.stdout
