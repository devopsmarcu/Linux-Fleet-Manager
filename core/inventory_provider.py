from __future__ import annotations

import os
from typing import Protocol, runtime_checkable
from pathlib import Path

from core.config import get_settings
from core.exceptions import InventoryError


@runtime_checkable
class InventoryProvider(Protocol):
    """Protocol defining how to provide inventory arguments to the Ansible executor."""

    def get_inventory_args(self) -> list[str]:
        """
        Returns the command-line arguments necessary for Ansible to load the inventory.
        Example: ['-i', '/path/to/hosts.ini']
        """
        ...


class IniInventoryProvider:
    """Default provider that uses a static INI file inventory."""

    def __init__(self, inventory_file: Path | None = None) -> None:
        settings = get_settings()
        # Preserve the existing resolution logic: settings.ansible_dir / "inventory" / settings.ansible_inventory_file
        self.inventory_file: Path = inventory_file or (
            settings.base_dir / "inventory" / settings.ansible_inventory_file
        )
        self._validate()

    def _validate(self) -> None:
        """Ensures the inventory file exists on disk."""
        if not self.inventory_file.is_file():
            raise InventoryError(
                "Arquivo de inventário não encontrado",
                path=str(self.inventory_file),
            )

    def get_inventory_args(self) -> list[str]:
        """Returns the -i flag and the path to the INI inventory file."""
        return ["-i", str(self.inventory_file)]


class DynamicInventoryProvider:
    """Provider that uses an executable script as a dynamic inventory source."""

    def __init__(self, script_path: Path | None = None) -> None:
        settings = get_settings()
        self.script_path: Path = script_path or settings.ansible_dynamic_inventory_script
        self._validate()

    def _validate(self) -> None:
        """Ensures the script exists and is executable."""
        if self.script_path is None:
            raise InventoryError(
                "Script de inventário dinâmico não configurado",
                detail="Defina 'ansible_dynamic_inventory_script' nas configurações.",
            )
        if not self.script_path.is_file():
            raise InventoryError(
                "Script de inventário dinâmico não encontrado",
                path=str(self.script_path),
            )
        if not os.access(self.script_path, os.X_OK):
            raise InventoryError(
                "Script de inventário dinâmico não tem permissão de execução",
                path=str(self.script_path),
            )

    def get_inventory_args(self) -> list[str]:
        """Returns the -i flag and the path to the dynamic inventory script."""
        return ["-i", str(self.script_path)]
