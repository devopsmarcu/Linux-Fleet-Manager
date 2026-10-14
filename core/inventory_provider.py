from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Protocol, runtime_checkable
from pathlib import Path

from core.config import get_settings
from core.exceptions import InventoryError
from core.logger import get_logger


logger = get_logger("inventory_provider")


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
            settings.ansible_dir / "inventory" / settings.ansible_inventory_file
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


class YamlInventoryProvider:
    """Provider that uses an Ansible YAML inventory plugin file (e.g., aws_ec2.yml, netbox.yml).

    Executes `ansible-inventory -i <plugin_file> --list` to resolve the plugin and parse JSON output.
    Supports local caching with TTL to avoid repeated API calls.
    """

    def __init__(
        self,
        plugin_file: Path | None = None,
        cache_ttl: int | None = None,
        cache_dir: Path | None = None,
        refresh_cache: bool = False,
    ) -> None:
        settings = get_settings()
        self.plugin_file: Path = plugin_file or settings.ansible_dynamic_inventory_plugin
        self.cache_ttl: int = cache_ttl or settings.ansible_inventory_cache_ttl
        self.cache_dir: Path = cache_dir or settings.ansible_inventory_cache_dir
        self.refresh_cache: bool = refresh_cache
        self._validate()

    def _validate(self) -> None:
        """Ensures the plugin file exists."""
        if self.plugin_file is None:
            raise InventoryError(
                "Plugin de inventário YAML não configurado",
                detail="Defina 'ansible_dynamic_inventory_plugin' nas configurações.",
            )
        if not self.plugin_file.is_file():
            raise InventoryError(
                "Plugin de inventário YAML não encontrado",
                path=str(self.plugin_file),
            )

    def get_inventory_args(self) -> list[str]:
        """Returns the -i flag and the path to the YAML plugin file."""
        return ["-i", str(self.plugin_file)]

    def fetch_inventory(self) -> dict:
        """Fetch inventory from plugin, using cache if valid."""
        cache_path = self._get_cache_path()

        # Check cache first (unless forced refresh)
        if not self.refresh_cache and cache_path.is_file():
            cached_data = self._load_cache(cache_path)
            if cached_data is not None:
                logger.debug(
                    "Cache hit para inventário YAML",
                    extra={"lfm_plugin": str(self.plugin_file), "lfm_cache": str(cache_path)},
                )
                return cached_data
            else:
                logger.debug(
                    "Cache expirado para inventário YAML",
                    extra={"lfm_plugin": str(self.plugin_file), "lfm_cache": str(cache_path)},
                )

        # Cache miss or expired: fetch from ansible-inventory
        logger.info(
            "Buscando inventário do plugin YAML (sem cache válido)",
            extra={"lfm_plugin": str(self.plugin_file)},
        )
        try:
            inventory_data = self._run_ansible_inventory()
            self._save_cache(cache_path, inventory_data)
            return inventory_data
        except Exception as exc:
            # Fallback: try to use expired cache
            if cache_path.is_file():
                cached_data = self._load_cache(cache_path, ignore_ttl=True)
                if cached_data is not None:
                    logger.warning(
                        "Falha ao buscar inventário; usando cache expirado como fallback",
                        extra={
                            "lfm_plugin": str(self.plugin_file),
                            "lfm_error": str(exc),
                            "lfm_cache": str(cache_path),
                        },
                    )
                    return cached_data
            raise

    def _run_ansible_inventory(self) -> dict:
        """Execute ansible-inventory -i <plugin> --list and parse JSON output."""
        cmd = ["ansible-inventory", "-i", str(self.plugin_file), "--list"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise InventoryError("Saída do ansible-inventory não é um dict JSON válido")
            return payload
        except subprocess.CalledProcessError as exc:
            raise InventoryError(
                "Erro ao executar ansible-inventory",
                detail=f"RC {exc.returncode}: {exc.stderr or exc.stdout}",
            )
        except subprocess.TimeoutExpired:
            raise InventoryError("Timeout ao executar ansible-inventory (60s)")
        except json.JSONDecodeError as exc:
            raise InventoryError(
                "Saída do ansible-inventory não é JSON válido",
                detail=str(exc),
            )

    def _get_cache_path(self) -> Path:
        """Generate cache file path based on plugin file hash."""
        import hashlib
        plugin_hash = hashlib.sha256(str(self.plugin_file).encode()).hexdigest()[:16]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"inventory_{plugin_hash}.json"

    def _load_cache(self, cache_path: Path, ignore_ttl: bool = False) -> dict | None:
        """Load cache if valid (respects TTL unless ignore_ttl=True)."""
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)

            if not isinstance(cached, dict):
                return None

            meta = cached.get("_lfm_cache_meta", {})
            cached_at = meta.get("cached_at", 0)
            ttl = meta.get("ttl", self.cache_ttl)

            if not ignore_ttl and (time.time() - cached_at) > ttl:
                return None

            # Return the actual inventory data (without meta)
            return {k: v for k, v in cached.items() if k != "_lfm_cache_meta"}
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def _save_cache(self, cache_path: Path, data: dict) -> None:
        """Save inventory data to cache with metadata."""
        try:
            cache_data = dict(data)
            cache_data["_lfm_cache_meta"] = {
                "cached_at": time.time(),
                "ttl": self.cache_ttl,
                "plugin_file": str(self.plugin_file),
            }
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(cache_data, f)
        except OSError as exc:
            logger.warning(
                "Falha ao salvar cache de inventário",
                extra={"lfm_cache": str(cache_path), "lfm_error": str(exc)},
            )