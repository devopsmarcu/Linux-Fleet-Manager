from __future__ import annotations

import pytest
from pathlib import Path
from typing import Protocol

from core.config import get_settings
from core.exceptions import InventoryError
from core.inventory_provider import IniInventoryProvider, InventoryProvider

class MockProvider:
    """Fake provider for testing injection."""
    def get_inventory_args(self) -> list[str]:
        return ["-i", "mock_inventory"]

def test_ini_provider_resolution():
    # Test that it resolves to the path in settings
    settings = get_settings()
    expected = settings.ansible_dir / "inventory" / settings.ansible_inventory_file
    
    provider = IniInventoryProvider()
    assert provider.inventory_file == expected
    assert provider.get_inventory_args() == ["-i", str(expected)]

def test_ini_provider_custom_file():
    # Test custom file path
    custom_path = Path("/tmp/custom_hosts.ini")
    custom_path.touch()
    try:
        provider = IniInventoryProvider(inventory_file=custom_path)
        assert provider.inventory_file == custom_path
        assert provider.get_inventory_args() == ["-i", str(custom_path)]
    finally:
        custom_path.unlink()

def test_ini_provider_missing_file():
    # Test validation when file is missing
    missing_path = Path("/tmp/non_existent_hosts.ini")
    with pytest.raises(InventoryError) as excinfo:
        IniInventoryProvider(inventory_file=missing_path)
    assert "Arquivo de inventário não encontrado" in str(excinfo.value)

def test_inventory_provider_protocol():
    # Verify that IniInventoryProvider implements the Protocol
    provider = IniInventoryProvider()
    assert isinstance(provider, InventoryProvider)
