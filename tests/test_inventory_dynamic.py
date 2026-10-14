from __future__ import annotations

import os
import json
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch

from core.config import Settings, get_settings
from core.exceptions import InventoryError
from core.inventory import InventoryManager
from core.inventory_provider import DynamicInventoryProvider, IniInventoryProvider
from ansible.runner import AnsibleExecutor

# Mock settings to avoid modifying global state
class MockSettings(Settings):
    ansible_inventory_provider: str = "dynamic"
    ansible_dynamic_inventory_script: Path | None = None
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

def test_dynamic_provider_args(dynamic_script):
    provider = DynamicInventoryProvider(script_path=dynamic_script)
    assert provider.get_inventory_args() == ["-i", str(dynamic_script)]

def test_dynamic_provider_missing_file():
    with pytest.raises(InventoryError) as exc:
        DynamicInventoryProvider(script_path=Path("/tmp/non_existent_script"))
    assert "não encontrado" in str(exc.value)

def test_dynamic_provider_no_exec_permission(tmp_path):
    script = tmp_path / "no_exec.py"
    script.write_text("print('hi')")
    script.chmod(0o644)
    with pytest.raises(InventoryError) as exc:
        DynamicInventoryProvider(script_path=script)
    assert "permissão de execução" in str(exc.value)

def test_inventory_manager_dynamic_loading(dynamic_script):
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
    script = tmp_path / "invalid.py"
    script.write_text("#!/usr/bin/env python3\nprint('not json')")
    script.chmod(0o755)

    with patch("core.inventory.get_settings") as mock_get:
        settings = MockSettings()
        settings.ansible_inventory_provider = "dynamic"
        settings.ansible_dynamic_inventory_script = script
        mock_get.return_value = settings

        with pytest.raises(InventoryError) as exc:
            InventoryManager()
        assert "não é um JSON válido" in str(exc.value)

def test_inventory_manager_script_failure(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(1)")
    script.chmod(0o755)

    with patch("core.inventory.get_settings") as mock_get:
        settings = MockSettings()
        settings.ansible_inventory_provider = "dynamic"
        settings.ansible_dynamic_inventory_script = script
        mock_get.return_value = settings

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
