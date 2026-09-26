from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from core.config import get_settings
from core.exceptions import InventoryError
from core.logger import get_logger
from core.models import Host, HostStatusEnum, HostSystemInfo, InventorySummary

logger = get_logger("inventory")

_GROUP_HEADER_RE = re.compile(r"^\[(.+)\]$")
_GROUP_CHILDREN_RE = re.compile(r"^(.+):children$")
_GROUP_VARS_RE = re.compile(r"^(.+):vars$")


class InventoryManager:
    """Carrega e gerencia o inventário Ansible (formato INI + group_vars/host_vars YAML).

    Suporta apenas subconjunto necessário do formato inventário INI do Ansible:
    - [grupo] com hosts e pares chave=valor
    - [grupo:children] para grupos compostos
    - [grupo:vars] para variáveis inline
    - Arquivos complementares em <inventory_dir>/group_vars/<grupo>.yml e host_vars/<host>.yml
    """

    def __init__(self, inventory_file: Path | None = None) -> None:
        settings = get_settings()
        self.inventory_file: Path = inventory_file or (
            settings.ansible_dir / "inventory" / settings.ansible_inventory_file
        )
        if not self.inventory_file.is_file():
            raise InventoryError(
                "Arquivo de inventário não encontrado",
                path=str(self.inventory_file),
            )
        self.inventory_dir = self.inventory_file.parent
        self._hosts: dict[str, Host] = {}
        self._groups: dict[str, set[str]] = {}
        self._group_vars: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def list_hosts(
        self,
        group: str | None = None,
        host_name: str | None = None,
    ) -> list[Host]:
        hosts = list(self._hosts.values())
        if group:
            if group not in self._groups:
                raise InventoryError(
                    "Grupo não existe no inventário",
                    group=group,
                    available=sorted(self._groups.keys()),
                )
            member_names = self._groups[group]
            hosts = [h for h in hosts if h.name in member_names]
        if host_name:
            hosts = [h for h in hosts if h.name == host_name]
        return sorted(hosts, key=lambda h: h.name)

    def list_groups(self) -> dict[str, int]:
        return {g: len(self._groups[g]) for g in sorted(self._groups)}

    def summary(self) -> InventorySummary:
        return InventorySummary(
            total_hosts=len(self._hosts),
            total_groups=len(self._groups),
            groups=self.list_groups(),
            hosts=self.list_hosts(),
        )

    def get_host(self, name: str) -> Host:
        try:
            return self._hosts[name]
        except KeyError as e:
            raise InventoryError("Host não encontrado", host=name) from e

    # ------------------------------------------------------------------
    # Carregamento
    # ------------------------------------------------------------------
    def _load(self) -> None:
        logger.debug(
            "Carregando inventário",
            extra={"lfm_inventory_file": str(self.inventory_file)},
        )
        parser = self._parse_ini()
        self._resolve_group_children(parser)
        self._load_external_group_vars()
        self._build_hosts(parser)
        self._load_external_host_vars()
        logger.info(
            "Inventário carregado com sucesso",
            extra={
                "lfm_hosts": len(self._hosts),
                "lfm_groups": len(self._groups),
            },
        )
        if not self._hosts:
            logger.warning(
                "Nenhum host ativo encontrado no inventário. Descomente entradas em hosts.ini "
                "ou adicione novas máquinas.",
                extra={"lfm_inventory_file": str(self.inventory_file)},
            )

    def _parse_ini(self) -> dict[str, dict]:
        parser: dict[str, dict] = {
            "groups": {},
            "group_vars_inline": {},
            "group_children": {},
        }
        current_group: str | None = None
        current_section_kind: str = "hosts"

        with self.inventory_file.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue

                header_match = _GROUP_HEADER_RE.match(line)
                if header_match:
                    header = header_match.group(1).strip()
                    children_match = _GROUP_CHILDREN_RE.match(header)
                    vars_match = _GROUP_VARS_RE.match(header)
                    if children_match:
                        current_group = children_match.group(1).strip()
                        current_section_kind = "children"
                        parser["group_children"].setdefault(current_group, [])
                    elif vars_match:
                        current_group = vars_match.group(1).strip()
                        current_section_kind = "vars"
                        parser["group_vars_inline"].setdefault(current_group, {})
                    else:
                        current_group = header
                        current_section_kind = "hosts"
                        parser["groups"].setdefault(current_group, {})
                        self._groups.setdefault(current_group, set())
                    continue

                if current_group is None:
                    continue

                if current_section_kind == "children":
                    child_group = line
                    parser["group_children"][current_group].append(child_group)
                    parser["groups"].setdefault(child_group, {})
                    self._groups.setdefault(child_group, set())
                elif current_section_kind == "vars":
                    key, _, value = line.partition("=")
                    parser["group_vars_inline"][current_group][key.strip()] = value.strip()
                else:
                    parts = line.split()
                    host_name = parts[0]
                    vars_map: dict[str, str] = {}
                    for part in parts[1:]:
                        if "=" in part:
                            k, v = part.split("=", 1)
                            vars_map[k.strip()] = v.strip()
                    parser["groups"].setdefault(current_group, {})[host_name] = vars_map
                    self._groups.setdefault(current_group, set()).add(host_name)
        return parser

    def _resolve_group_children(self, parser: dict) -> None:
        def expand(group_name: str, visited: set[str]) -> set[str]:
            if group_name in visited:
                return self._groups.get(group_name, set())
            visited.add(group_name)
            direct = set(self._groups.get(group_name, set()))
            for child in parser["group_children"].get(group_name, []):
                direct |= expand(child, visited)
            return direct

        for group_name in list(parser["group_children"].keys()):
            self._groups[group_name] = expand(group_name, set())

    def _load_external_group_vars(self) -> None:
        group_vars_dir = self.inventory_dir / "group_vars"
        if not group_vars_dir.is_dir():
            return
        for path in sorted(group_vars_dir.iterdir()):
            if not path.is_file() or path.suffix not in (".yml", ".yaml"):
                continue
            group_name = path.stem
            self._group_vars.setdefault(group_name, {})
            with path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    self._group_vars[group_name].update(loaded)

    def _build_hosts(self, parser: dict) -> None:
        all_host_names: set[str] = set()
        for hosts in self._groups.values():
            all_host_names |= hosts

        for host_name in all_host_names:
            groups: list[str] = sorted(
                [g for g, members in self._groups.items() if host_name in members]
            )
            host_vars: dict = {}
            address = host_name
            port = 22
            user: str | None = None

            for g in groups:
                host_vars.update(self._group_vars.get(g, {}))
                host_vars.update(parser["group_vars_inline"].get(g, {}))
                host_entries = parser["groups"].get(g, {})
                inline = host_entries.get(host_name, {})
                host_vars.update(inline)
                address = inline.get("ansible_host", address)
                user = inline.get("ansible_user") or host_vars.get("ansible_user") or user
                port = int(inline.get("ansible_port", host_vars.get("ansible_port", port)))

            self._hosts[host_name] = Host(
                name=host_name,
                address=address,
                groups=groups,
                vars=host_vars,
                user=user,
                port=port,
            )

    def _load_external_host_vars(self) -> None:
        host_vars_dir = self.inventory_dir / "host_vars"
        if not host_vars_dir.is_dir():
            return
        for path in sorted(host_vars_dir.iterdir()):
            if not path.is_file() or path.suffix not in (".yml", ".yaml"):
                continue
            host_name = path.stem
            if host_name not in self._hosts:
                continue
            with path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    self._hosts[host_name].vars.update(loaded)
                    if "ansible_user" in loaded:
                        self._hosts[host_name].user = loaded["ansible_user"]
                    if "ansible_port" in loaded:
                        self._hosts[host_name].port = int(loaded["ansible_port"])


class SystemInfoCollector:
    """Coleta e normaliza informações detalhadas dos hosts via Ansible playbook collect_info.yml."""

    def __init__(
        self,
        inventory_manager: InventoryManager | None = None,
        executor: Any | None = None,
    ) -> None:
        self.inventory_manager = inventory_manager or InventoryManager()
        if executor is None:
            from ansible.runner import AnsibleExecutor
            self.executor = AnsibleExecutor(inventory_file=self.inventory_manager.inventory_file)
        else:
            self.executor = executor

    def collect(
        self,
        targets: str = "all",
        group: str | None = None,
        host_name: str | None = None,
        timeout: int | None = None,
    ) -> list[HostSystemInfo]:
        """Executa a coleta no playbook e retorna lista de HostSystemInfo estruturados."""
        target_pattern = host_name or group or targets or "all"
        known_hosts = {h.name: h for h in self.inventory_manager.list_hosts()}

        try:
            expected_hosts = self.inventory_manager.list_hosts(group=group, host_name=host_name)
        except Exception:
            expected_hosts = list(known_hosts.values())

        logger.info(
            "Iniciando coleta de informações do inventário LFM",
            extra={"lfm_targets": target_pattern, "lfm_timeout": timeout},
        )

        try:
            op_result = self.executor.playbook(
                playbook="collect_info.yml",
                targets=target_pattern,
                timeout=timeout,
                become=False,
            )
        except Exception as exc:
            logger.error(
                "Falha na execução do playbook de coleta",
                extra={"lfm_error": str(exc)},
            )
            op_result = None

        collected_map: dict[str, HostSystemInfo] = {}

        if op_result:
            for task in op_result.task_results:
                h_name = task.host
                host_obj = known_hosts.get(h_name, Host(name=h_name, address=h_name))

                if task.unreachable or task.failed:
                    default_err = "Host inalcançável" if task.unreachable else "Falha na coleta"
                    msg = task.msg or task.stderr or default_err
                    collected_map[h_name] = HostSystemInfo(
                        host=h_name,
                        address=host_obj.address,
                        status=HostStatusEnum.OFFLINE if task.unreachable else HostStatusEnum.ERROR,
                        error_message=msg,
                    )
                else:
                    task_data = task.data if isinstance(task.data, dict) else {}
                    raw_facts = task_data.get("ansible_facts")
                    facts = raw_facts if isinstance(raw_facts, dict) else {}
                    sys_info = facts.get("lfm_system_info") or task_data.get("lfm_system_info")
                    if sys_info and isinstance(sys_info, dict):
                        collected_map[h_name] = self.normalize_host_info(h_name, host_obj, sys_info)
                    elif h_name not in collected_map:
                        collected_map[h_name] = HostSystemInfo(
                            host=h_name,
                            address=host_obj.address,
                            status=HostStatusEnum.ONLINE,
                        )

        # Garantir que todos os hosts esperados pelo filtro apareçam na lista final
        for h in expected_hosts:
            if h.name not in collected_map:
                collected_map[h.name] = HostSystemInfo(
                    host=h.name,
                    address=h.address,
                    status=HostStatusEnum.OFFLINE,
                    error_message="Sem resposta do Ansible",
                )

        return sorted(collected_map.values(), key=lambda s: s.host)

    @staticmethod
    def normalize_host_info(
        host_name: str,
        host_obj: Host,
        raw_info: dict[str, Any],
    ) -> HostSystemInfo:
        """Normaliza dict bruto de fatos em objeto HostSystemInfo tipado."""
        mounts = raw_info.get("mounts") or []
        disk_total_gb: float | None = None
        disk_used_gb: float | None = None

        root_mount = None
        for m in mounts:
            if isinstance(m, dict) and m.get("mount") == "/":
                root_mount = m
                break

        target_mount = root_mount or (mounts[0] if mounts and isinstance(mounts[0], dict) else None)
        if target_mount:
            total_b = target_mount.get("size_total") or (
                target_mount.get("block_total", 0) * target_mount.get("block_size", 0)
            )
            avail_b = target_mount.get("size_available") or (
                target_mount.get("block_available", 0) * target_mount.get("block_size", 0)
            )
            if total_b and total_b > 0:
                disk_total_gb = round(total_b / (1024**3), 1)
                disk_used_gb = round((total_b - avail_b) / (1024**3), 1)

        raw_updates = raw_info.get("pending_updates", 0)
        try:
            pending_updates = int(raw_updates)
        except (ValueError, TypeError):
            pending_updates = 0

        try:
            mem_total = int(raw_info.get("memory_total_mb", 0) or 0)
        except (ValueError, TypeError):
            mem_total = 0

        try:
            cpu_cores = int(raw_info.get("cpu_cores", 0) or 0)
        except (ValueError, TypeError):
            cpu_cores = 1

        try:
            uptime = int(raw_info.get("uptime_seconds", 0) or 0)
        except (ValueError, TypeError):
            uptime = 0

        return HostSystemInfo(
            host=host_name,
            address=str(raw_info.get("ip") or host_obj.address),
            status=HostStatusEnum.ONLINE,
            hostname=str(raw_info.get("hostname") or host_name),
            ip=str(raw_info.get("ip") or host_obj.address),
            distribution=str(raw_info.get("distribution") or "Linux"),
            distribution_version=str(raw_info.get("distribution_version") or ""),
            kernel=str(raw_info.get("kernel") or ""),
            architecture=str(raw_info.get("architecture") or ""),
            cpu_model=str(raw_info.get("cpu_model") or ""),
            cpu_cores=cpu_cores,
            memory_total_mb=mem_total,
            disk_total_gb=disk_total_gb,
            disk_used_gb=disk_used_gb,
            uptime_seconds=uptime,
            remote_user=str(raw_info.get("remote_user") or host_obj.user or ""),
            python_version=str(raw_info.get("python_version") or ""),
            services=list(raw_info.get("services") or []),
            pending_updates=pending_updates,
        )
