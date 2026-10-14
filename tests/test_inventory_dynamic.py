from __future__ import annotations

import json
import os
import time
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.config import Settings, get_settings
from core.exceptions import InventoryError
from core.inventory import InventoryManager
from core.inventory_provider import (
    DynamicInventoryProvider,
    IniInventoryProvider,
    YamlInventoryProvider,
)
from ansible.runner import AnsibleExecutor

# Mock settings to avoid modifying global state
class MockSettings(Settings):
    ansible_inventory_provider: str = "dynamic"
    ansible_dynamic_inventory_script: Path | None = None
    ansible_dynamic_inventory_plugin: Path | None = None
    ansible_inventory_cache_ttl: int = 300
    ansible_inventory_cache_dir: Path = Path("/tmp/lfm_test_cache")
    ansible_dir: Path = Path("/tmp/lfm_test")
    ansible_inventory_file: str = "hosts.ini"

@pytest.fixture
def mock_settings():
    # Use a fresh instance for each test to avoid leakage
    settings = MockSettings()
    # We patch the get_settings calls in the modules where the classes being tested reside
    with patch("core.inventory_provider.get_settings", return_value=settings), \
         patch("ansible.runner.get_settings", return_value=settings), \
         patch("core.inventory.get_settings", return_value=settings):
        yield settings

@pytest.fixture
def dynamic_script(tmp_path):
    script = tmp_path / "inventory.py"
    content = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\n"
        "    'web': {'hosts': ['web01', 'web02'], 'vars': {'http_port': 80}},\n"
        "    'db': {'hosts': ['db01'], 'vars': {'db_port': 5432}},\n"
        "    '_meta': {'hostvars': {'web01': {'ansible_host': '1.1.1.1'}, 'web02': {'ansible_host': '1.1.1.2'}, 'db01': {'ansible_host': '1.1.1.3'}}}\n"
        "}))\n"
    )
    script.write_text(content)
    script.chmod(0o755)
    return script

@pytest.fixture
def yaml_plugin_file(tmp_path):
    """Create a mock YAML plugin file for testing."""
    plugin = tmp_path / "aws_ec2.yml"
    content = (
        "plugin: amazon.aws.aws_ec2\n"
        "regions:\n"
        "  - us-east-1\n"
        "keyed_groups:\n"
        "  - prefix: tag\n"
        "    key: Environment\n"
    )
    plugin.write_text(content)
    return plugin

@pytest.fixture
def mock_ansible_inventory_output():
    """Mock output from ansible-inventory --list command."""
    return {
        "web": {"hosts": ["web01", "web02"], "vars": {"http_port": 80}},
        "db": {"hosts": ["db01"], "vars": {"db_port": 5432}},
        "_meta": {
            "hostvars": {
                "web01": {"ansible_host": "1.1.1.1", "ansible_user": "ubuntu"},
                "web02": {"ansible_host": "1.1.1.2", "ansible_user": "ubuntu"},
                "db01": {"ansible_host": "1.1.1.3", "ansible_user": "admin"},
            }
        },
    }


def test_dynamic_provider_args(dynamic_script):
    provider = DynamicInventoryProvider(script_path=dynamic_script)
    assert provider.get_inventory_args() == ["-i", str(dynamic_script)]


def test_dynamic_provider_missing_file():
    with pytest.raises(InventoryError) as exc:
        DynamicInventoryProvider(script_path=Path("/tmp/non_existent_script"))
    assert "não encontrado" in str(exc.value)


def test_dynamic_provider_no_exec_permission(tmp_path):
    """Test DynamicInventoryProvider validates execute permission (Unix only)."""
    import sys
    if sys.platform == "win32":
        pytest.skip("Execute permission check not applicable on Windows")
    script = tmp_path / "no_exec.py"
    script.write_text("print('hi')")
    script.chmod(0o644)
    with pytest.raises(InventoryError) as exc:
        DynamicInventoryProvider(script_path=script)
    assert "permissão de execução" in str(exc.value)


def test_inventory_manager_dynamic_loading(dynamic_script):
    """Test InventoryManager loading from dynamic script (mocked on Windows)."""
    import sys
    if sys.platform == "win32":
        # On Windows, we can't execute Python scripts directly, so mock subprocess
        with patch("core.inventory.get_settings") as mock_get, \
             patch("subprocess.run") as mock_run:
            settings = MockSettings()
            settings.ansible_inventory_provider = "dynamic"
            settings.ansible_dynamic_inventory_script = dynamic_script
            mock_get.return_value = settings

            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "web": {"hosts": ["web01", "web02"], "vars": {"http_port": 80}},
                    "db": {"hosts": ["db01"], "vars": {"db_port": 5432}},
                    "_meta": {"hostvars": {"web01": {"ansible_host": "1.1.1.1"}, "web02": {"ansible_host": "1.1.1.2"}, "db01": {"ansible_host": "1.1.1.3"}}}
                }),
                stderr="",
                returncode=0,
            )

            mgr = InventoryManager()
            assert len(mgr.list_hosts()) == 3
            assert "web01" in [h.name for h in mgr.list_hosts()]
            assert mgr.list_groups()["web"] == 2
    else:
        with patch("core.inventory.get_settings") as mock_get:
            settings = MockSettings()
            settings.ansible_inventory_provider = "dynamic"
            settings.ansible_dynamic_inventory_script = dynamic_script
            mock_get.return_value = settings

            mgr = InventoryManager()
            assert len(mgr.list_hosts()) == 3
            assert "web01" in [h.name for h in mgr.list_hosts()]
            assert mgr.list_groups()["web"] == 2


def test_inventory_manager_invalid_json(tmp_path):
    """Test InventoryManager handles invalid JSON from dynamic script."""
    import sys
    script = tmp_path / "invalid.py"
    script.write_text("#!/usr/bin/env python3\nprint('not json')")
    script.chmod(0o755)

    with patch("core.inventory.get_settings") as mock_get, \
         patch("subprocess.run") as mock_run:
        settings = MockSettings()
        settings.ansible_inventory_provider = "dynamic"
        settings.ansible_dynamic_inventory_script = script
        mock_get.return_value = settings

        mock_run.return_value = MagicMock(
            stdout="not json",
            stderr="",
            returncode=0,
        )

        with pytest.raises(InventoryError) as exc:
            InventoryManager()
        assert "não é um JSON válido" in str(exc.value)


def test_inventory_manager_script_failure(tmp_path):
    """Test InventoryManager handles script execution failure."""
    import sys
    script = tmp_path / "fail.py"
    script.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(1)")
    script.chmod(0o755)

    with patch("core.inventory.get_settings") as mock_get, \
         patch("subprocess.run") as mock_run:
        settings = MockSettings()
        settings.ansible_inventory_provider = "dynamic"
        settings.ansible_dynamic_inventory_script = script
        mock_get.return_value = settings

        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="Script failed")

        with pytest.raises(InventoryError) as exc:
            InventoryManager()
        assert "Erro ao executar script" in str(exc.value)


def test_executor_dynamic_provider_selection(mock_settings, dynamic_script):
    mock_settings.ansible_inventory_provider = "dynamic"
    mock_settings.ansible_dynamic_inventory_script = dynamic_script

    executor = AnsibleExecutor()
    assert isinstance(executor.inventory_provider, DynamicInventoryProvider)
    assert executor.inventory_provider.get_inventory_args() == ["-i", str(dynamic_script)]


def test_executor_default_ini_provider(mock_settings, tmp_path):
    # Setup a dummy hosts.ini so the validator doesn't fail
    # The IniInventoryProvider resolves to: base_dir / "inventory" / ansible_inventory_file
    inventory_dir = tmp_path / "inventory"
    inventory_dir.mkdir()
    hosts_ini = inventory_dir / "hosts.ini"
    hosts_ini.write_text("[all]\nlocalhost ansible_connection=local")

    mock_settings.ansible_inventory_provider = "ini"
    mock_settings.ansible_dir = tmp_path
    mock_settings.ansible_inventory_file = "hosts.ini"

    executor = AnsibleExecutor()
    assert isinstance(executor.inventory_provider, IniInventoryProvider)


# =============================================================================
# NEW TESTS: YamlInventoryProvider and Cache
# =============================================================================

def test_yaml_provider_args(yaml_plugin_file):
    """Test YamlInventoryProvider returns correct ansible-inventory args."""
    provider = YamlInventoryProvider(plugin_file=yaml_plugin_file, cache_ttl=300)
    assert provider.get_inventory_args() == ["-i", str(yaml_plugin_file)]


def test_yaml_provider_missing_file():
    """Test YamlInventoryProvider raises error for missing plugin file."""
    with pytest.raises(InventoryError) as exc:
        YamlInventoryProvider(plugin_file=Path("/tmp/non_existent.yml"))
    assert "não encontrado" in str(exc.value)


def test_yaml_provider_not_configured():
    """Test YamlInventoryProvider raises error when plugin not configured."""
    with pytest.raises(InventoryError) as exc:
        YamlInventoryProvider(plugin_file=None)
    assert "não configurado" in str(exc.value)


def test_yaml_provider_cache_hit(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test cache hit: returns cached data without calling ansible-inventory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Pre-populate cache
    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=False,
    )

    import hashlib
    plugin_hash = hashlib.sha256(str(yaml_plugin_file).encode()).hexdigest()[:16]
    cache_path = cache_dir / f"inventory_{plugin_hash}.json"

    cache_data = dict(mock_ansible_inventory_output)
    cache_data["_lfm_cache_meta"] = {
        "cached_at": time.time(),
        "ttl": 300,
        "plugin_file": str(yaml_plugin_file),
    }
    cache_path.write_text(json.dumps(cache_data))

    with patch("subprocess.run") as mock_run:
        result = provider.fetch_inventory()
        mock_run.assert_not_called()  # Should not call ansible-inventory

    assert result == mock_ansible_inventory_output


def test_yaml_provider_cache_miss(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test cache miss: calls ansible-inventory when no cache exists."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=False,
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=json.dumps(mock_ansible_inventory_output),
            stderr="",
            returncode=0,
        )
        result = provider.fetch_inventory()

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["ansible-inventory", "-i", str(yaml_plugin_file), "--list"]

    assert result == mock_ansible_inventory_output


def test_yaml_provider_cache_expired_fallback(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test fallback to expired cache when ansible-inventory fails."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create expired cache
    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=False,
    )

    import hashlib
    plugin_hash = hashlib.sha256(str(yaml_plugin_file).encode()).hexdigest()[:16]
    cache_path = cache_dir / f"inventory_{plugin_hash}.json"

    cache_data = dict(mock_ansible_inventory_output)
    cache_data["_lfm_cache_meta"] = {
        "cached_at": time.time() - 1000,  # Expired (1000 seconds ago)
        "ttl": 300,
        "plugin_file": str(yaml_plugin_file),
    }
    cache_path.write_text(json.dumps(cache_data))

    # Mock ansible-inventory to fail
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="API error")

        # Should fallback to expired cache
        result = provider.fetch_inventory()

    assert result == mock_ansible_inventory_output


def test_yaml_provider_refresh_cache_flag(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test refresh_cache=True forces ansible-inventory call even with valid cache."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Pre-populate valid cache
    import hashlib
    plugin_hash = hashlib.sha256(str(yaml_plugin_file).encode()).hexdigest()[:16]
    cache_path = cache_dir / f"inventory_{plugin_hash}.json"

    cache_data = dict(mock_ansible_inventory_output)
    cache_data["_lfm_cache_meta"] = {
        "cached_at": time.time(),
        "ttl": 300,
        "plugin_file": str(yaml_plugin_file),
    }
    cache_path.write_text(json.dumps(cache_data))

    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=True,  # Force refresh
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=json.dumps(mock_ansible_inventory_output),
            stderr="",
            returncode=0,
        )
        result = provider.fetch_inventory()

        mock_run.assert_called_once()  # Should call ansible-inventory despite valid cache

    assert result == mock_ansible_inventory_output


def test_yaml_provider_ansible_inventory_timeout(tmp_path, yaml_plugin_file):
    """Test timeout handling when ansible-inventory hangs."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=False,
    )

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)

        with pytest.raises(InventoryError) as exc:
            provider.fetch_inventory()
        assert "Timeout" in str(exc.value)


def test_yaml_provider_invalid_json_output(tmp_path, yaml_plugin_file):
    """Test handling of invalid JSON from ansible-inventory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    provider = YamlInventoryProvider(
        plugin_file=yaml_plugin_file,
        cache_ttl=300,
        cache_dir=cache_dir,
        refresh_cache=False,
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="not json",
            stderr="",
            returncode=0,
        )

        with pytest.raises(InventoryError) as exc:
            provider.fetch_inventory()
        assert "não é JSON válido" in str(exc.value)


def test_inventory_manager_yaml_loading(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test InventoryManager loading from YAML plugin via YamlInventoryProvider."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with patch("core.inventory.get_settings") as mock_get:
        settings = MockSettings()
        settings.ansible_inventory_provider = "yaml"
        settings.ansible_dynamic_inventory_plugin = yaml_plugin_file
        settings.ansible_inventory_cache_ttl = 300
        settings.ansible_inventory_cache_dir = cache_dir
        mock_get.return_value = settings

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(mock_ansible_inventory_output),
                stderr="",
                returncode=0,
            )
            mgr = InventoryManager()

    assert len(mgr.list_hosts()) == 3
    assert "web01" in [h.name for h in mgr.list_hosts()]
    assert mgr.list_groups()["web"] == 2
    # Check host variables
    web01 = mgr.get_host("web01")
    assert web01.address == "1.1.1.1"
    assert web01.user == "ubuntu"
    assert web01.port == 22


def test_inventory_manager_yaml_with_refresh_cache(tmp_path, yaml_plugin_file, mock_ansible_inventory_output):
    """Test InventoryManager with refresh_cache=True."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with patch("core.inventory.get_settings") as mock_get:
        settings = MockSettings()
        settings.ansible_inventory_provider = "yaml"
        settings.ansible_dynamic_inventory_plugin = yaml_plugin_file
        settings.ansible_inventory_cache_ttl = 300
        settings.ansible_inventory_cache_dir = cache_dir
        mock_get.return_value = settings

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(mock_ansible_inventory_output),
                stderr="",
                returncode=0,
            )
            # Pass refresh_cache=True to constructor
            mgr = InventoryManager(refresh_cache=True)

    assert len(mgr.list_hosts()) == 3


def test_executor_yaml_provider_selection(mock_settings, yaml_plugin_file, mock_ansible_inventory_output):
    """Test AnsibleExecutor selects YamlInventoryProvider when provider is 'yaml'."""
    cache_dir = Path("/tmp/lfm_test_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    mock_settings.ansible_inventory_provider = "yaml"
    mock_settings.ansible_dynamic_inventory_plugin = yaml_plugin_file
    mock_settings.ansible_inventory_cache_dir = cache_dir
    # Provide a dummy hosts.ini for the fallback IniInventoryProvider validation
    mock_settings.ansible_dir = cache_dir
    mock_settings.ansible_inventory_file = "hosts.ini"
    (cache_dir / "inventory").mkdir(parents=True, exist_ok=True)
    (cache_dir / "inventory" / "hosts.ini").write_text("[all]\nlocalhost ansible_connection=local")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=json.dumps(mock_ansible_inventory_output),
            stderr="",
            returncode=0,
        )
        executor = AnsibleExecutor()

    assert isinstance(executor.inventory_provider, YamlInventoryProvider)
    assert executor.inventory_provider.get_inventory_args() == ["-i", str(yaml_plugin_file)]