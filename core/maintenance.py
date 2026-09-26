"""core/maintenance.py — Serviço de manutenção do LFM.

Responsabilidades:
- Executar o playbook maintenance.yml via AnsibleExecutor.
- Avaliar 8 verificações de saúde e estado por host.
- Construir MaintenanceReport estruturado.
- Logar cada verificação individualmente.

Não contém lógica de apresentação.
"""
from __future__ import annotations

from typing import Any

from core.exceptions import InventoryError
from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import (
    AnsibleOperationResult,
    Host,
    MaintenanceCheckItem,
    MaintenanceCheckState,
    MaintenanceHostReport,
    MaintenanceReport,
    _bytes_human,
)

logger = get_logger("maintenance")

# Limiares de aviso/crítico para disco (%)
_DISK_WARN_PCT = 80.0
_DISK_CRIT_PCT = 95.0

# Limiares de aviso/crítico para memória (%)
_MEM_WARN_PCT = 85.0
_MEM_CRIT_PCT = 95.0

# Carga de CPU: razão load_1m / cpu_cores
_CPU_WARN_RATIO = 1.5
_CPU_CRIT_RATIO = 3.0

# Pendentes de atualização
_UPDATE_WARN_COUNT = 20

# Cache de pacotes grande (bytes) — aviso informativo
_CACHE_WARN_BYTES = 1 * 1024 ** 3  # 1 GB


class MaintenanceRunner:
    """Serviço que orquestra o ciclo completo de manutenção dos hosts.

    1. Executa playbook maintenance.yml (coleta dados, sem destruir)
    2. Avalia cada métrica e atribui MaintenanceCheckState
    3. Constrói relatório estruturado por host
    4. Loga cada resultado
    """

    def __init__(
        self,
        inventory_manager: InventoryManager | None = None,
        executor: Any | None = None,
    ) -> None:
        self.inventory_manager = inventory_manager or InventoryManager()
        if executor is None:
            from ansible.runner import AnsibleExecutor
            self.executor = AnsibleExecutor(
                inventory_file=self.inventory_manager.inventory_file
            )
        else:
            self.executor = executor

    def run_maintenance(
        self,
        host_name: str | None = None,
        group: str | None = None,
        clean: bool = False,
        timeout: int | None = None,
    ) -> MaintenanceReport:
        """Executa manutenção e retorna relatório agregado."""
        target_pattern = host_name or group or "all"
        known_hosts = {h.name: h for h in self.inventory_manager.list_hosts()}

        try:
            expected_hosts = self.inventory_manager.list_hosts(
                group=group, host_name=host_name
            )
        except InventoryError:
            expected_hosts = list(known_hosts.values())

        logger.info(
            "Iniciando ciclo de manutenção",
            extra={
                "lfm_targets": target_pattern,
                "lfm_clean": clean,
                "lfm_timeout": timeout,
            },
        )

        report = MaintenanceReport(
            targets=target_pattern,
            clean_requested=clean,
        )

        extra_vars: dict[str, Any] = {
            "lfm_targets": target_pattern,
            "lfm_do_clean": "true" if clean else "false",
        }

        try:
            op_result: AnsibleOperationResult | None = self.executor.playbook(
                playbook="maintenance.yml",
                targets=target_pattern,
                become=False,
                extra_vars=extra_vars,
                timeout=timeout,
            )
        except Exception as exc:
            logger.error(
                "Falha na execução do playbook de manutenção",
                extra={"lfm_error": str(exc)},
            )
            op_result = None

        if op_result:
            report.host_reports = self._parse_host_reports(op_result, known_hosts)
        else:
            for h in expected_hosts:
                report.host_reports.append(
                    MaintenanceHostReport(
                        host=h.name,
                        address=h.address,
                        overall_state=MaintenanceCheckState.CRITICAL,
                        error="Executor não disponível ou host inalcançável",
                    )
                )

        report.finalize()

        for rep in report.host_reports:
            logger.info(
                "Relatório de manutenção do host",
                extra={
                    "lfm_host": rep.host,
                    "lfm_state": rep.overall_state.value,
                    "lfm_pending_updates": rep.pending_updates,
                    "lfm_cache_bytes": rep.cache_bytes,
                    "lfm_failed_services": len(rep.failed_services),
                },
            )

        return report

    @staticmethod
    def _parse_host_reports(
        op_result: AnsibleOperationResult,
        known_hosts: dict[str, Host],
    ) -> list[MaintenanceHostReport]:
        """Converte resultados do Ansible em MaintenanceHostReport por host."""
        reports: dict[str, MaintenanceHostReport] = {}

        for task in op_result.task_results:
            h_name = task.host

            if h_name in reports:
                # Atualizar com falha se uma task posterior falhar
                if task.failed or task.unreachable:
                    rep = reports[h_name]
                    rep.overall_state = MaintenanceCheckState.CRITICAL
                    rep.error = task.msg or task.stderr or "Falha em tarefa"
                continue

            host_obj = known_hosts.get(h_name, Host(name=h_name, address=h_name))

            if task.unreachable or task.failed:
                err_msg = task.msg or task.stderr or "Host inalcançável"
                reports[h_name] = MaintenanceHostReport(
                    host=h_name,
                    address=host_obj.address,
                    overall_state=MaintenanceCheckState.CRITICAL,
                    error=err_msg,
                )
            else:
                maint_data = _extract_maintenance_fact(task.data)
                if maint_data:
                    reports[h_name] = MaintenanceRunner.evaluate_host_data(
                        h_name, host_obj, maint_data
                    )
                else:
                    # Task de setup/gather_facts sem dado LFM — ignorar
                    pass

        return sorted(reports.values(), key=lambda r: r.host)

    @staticmethod
    def evaluate_host_data(
        host_name: str,
        host_obj: Host,
        data: dict[str, Any],
    ) -> MaintenanceHostReport:
        """Avalia os dados de manutenção e produz relatório com checks individuais."""
        checks: list[MaintenanceCheckItem] = []
        worst = MaintenanceCheckState.OK

        def _update_worst(state: MaintenanceCheckState) -> None:
            nonlocal worst
            _order = {
                MaintenanceCheckState.OK: 0,
                MaintenanceCheckState.SKIPPED: 0,
                MaintenanceCheckState.WARNING: 1,
                MaintenanceCheckState.CRITICAL: 2,
            }
            if _order.get(state, 0) > _order.get(worst, 0):
                worst = state

        # --- SSH / Connectivity ---
        checks.append(MaintenanceCheckItem(name="SSH", state=MaintenanceCheckState.OK, value="OK"))

        # --- Disco ---
        mounts = data.get("mounts") or []
        root_mount = next(
            (m for m in mounts if isinstance(m, dict) and m.get("mount") == "/"),
            mounts[0] if mounts and isinstance(mounts[0], dict) else None,
        )
        if root_mount:
            total = root_mount.get("size_total") or (
                root_mount.get("block_total", 0) * root_mount.get("block_size", 0)
            )
            avail = root_mount.get("size_available") or (
                root_mount.get("block_available", 0) * root_mount.get("block_size", 0)
            )
            if total and total > 0:
                used_pct = round(((total - avail) / total) * 100, 1)
                used_gb = (total - avail) / 1024 ** 3
                total_gb = total / 1024 ** 3
                disk_value = f"{used_gb:.1f}/{total_gb:.1f} GB ({used_pct:.0f}%)"
                if used_pct >= _DISK_CRIT_PCT:
                    st = MaintenanceCheckState.CRITICAL
                    _update_worst(st)
                elif used_pct >= _DISK_WARN_PCT:
                    st = MaintenanceCheckState.WARNING
                    _update_worst(st)
                else:
                    st = MaintenanceCheckState.OK
                checks.append(MaintenanceCheckItem(name="Disk", state=st, value=disk_value))
            else:
                checks.append(MaintenanceCheckItem(name="Disk", state=MaintenanceCheckState.OK, value="OK"))
        else:
            checks.append(MaintenanceCheckItem(name="Disk", state=MaintenanceCheckState.SKIPPED, value="-"))

        # --- Memória ---
        try:
            mem_total = float(data.get("mem_total_mb") or 0)
            mem_avail = float(data.get("mem_available_mb") or 0)
            if mem_total > 0:
                used_pct = round(((mem_total - mem_avail) / mem_total) * 100, 1)
                mem_value = f"{mem_avail:.0f}/{mem_total:.0f} MB ({used_pct:.0f}% used)"
                if used_pct >= _MEM_CRIT_PCT:
                    st = MaintenanceCheckState.CRITICAL
                    _update_worst(st)
                elif used_pct >= _MEM_WARN_PCT:
                    st = MaintenanceCheckState.WARNING
                    _update_worst(st)
                else:
                    st = MaintenanceCheckState.OK
                checks.append(MaintenanceCheckItem(name="Memory", state=st, value=mem_value))
            else:
                checks.append(MaintenanceCheckItem(name="Memory", state=MaintenanceCheckState.SKIPPED, value="-"))
        except (ValueError, TypeError):
            checks.append(MaintenanceCheckItem(name="Memory", state=MaintenanceCheckState.SKIPPED, value="-"))

        # --- Serviços ---
        failed_services = data.get("failed_services") or []
        if failed_services:
            svc_value = f"{len(failed_services)} failed: {', '.join(failed_services[:3])}"
            st = MaintenanceCheckState.WARNING
            _update_worst(st)
            checks.append(MaintenanceCheckItem(name="Services", state=st, value=svc_value))
        else:
            active = int(data.get("active_services") or 0)
            checks.append(MaintenanceCheckItem(
                name="Services",
                state=MaintenanceCheckState.OK,
                value=f"{active} active" if active > 0 else "OK",
            ))

        # --- Atualizações ---
        try:
            pending = int(data.get("pending_updates") or 0)
            upd_value = str(pending) if pending > 0 else "up-to-date"
            if pending >= _UPDATE_WARN_COUNT:
                st = MaintenanceCheckState.WARNING
                _update_worst(st)
            else:
                st = MaintenanceCheckState.OK
            checks.append(MaintenanceCheckItem(name="Updates", state=st, value=upd_value))
        except (ValueError, TypeError):
            checks.append(MaintenanceCheckItem(name="Updates", state=MaintenanceCheckState.SKIPPED, value="-"))
            pending = 0

        # --- Cache ---
        try:
            cache_bytes = int(data.get("cache_bytes") or 0)
            cache_value = _bytes_human(cache_bytes)
            if cache_bytes >= _CACHE_WARN_BYTES:
                st = MaintenanceCheckState.WARNING
                _update_worst(st)
            else:
                st = MaintenanceCheckState.OK
            checks.append(MaintenanceCheckItem(name="Cache", state=st, value=cache_value))
        except (ValueError, TypeError):
            cache_bytes = 0
            checks.append(MaintenanceCheckItem(name="Cache", state=MaintenanceCheckState.SKIPPED, value="-"))

        # --- Temporários ---
        try:
            tmp_bytes = int(data.get("tmp_bytes") or 0)
            tmp_value = _bytes_human(tmp_bytes)
            checks.append(MaintenanceCheckItem(name="Temp", state=MaintenanceCheckState.OK, value=tmp_value))
        except (ValueError, TypeError):
            tmp_bytes = 0
            checks.append(MaintenanceCheckItem(name="Temp", state=MaintenanceCheckState.SKIPPED, value="-"))

        return MaintenanceHostReport(
            host=host_name,
            address=str(data.get("default_ip") or host_obj.address),
            overall_state=worst,
            checks=checks,
            distribution=data.get("distribution"),
            pending_updates=int(data.get("pending_updates") or 0),
            cache_bytes=int(data.get("cache_bytes") or 0),
            tmp_bytes=int(data.get("tmp_bytes") or 0),
            failed_services=list(data.get("failed_services") or []),
            active_services=int(data.get("active_services") or 0),
            orphan_packages=int(data.get("orphan_packages") or 0),
        )


def _extract_maintenance_fact(task_data: dict[str, Any]) -> dict[str, Any]:
    """Extrai lfm_maintenance_data do task.data."""
    if not task_data:
        return {}
    facts = task_data.get("ansible_facts") or {}
    fact = facts.get("lfm_maintenance_data") or task_data.get("lfm_maintenance_data")
    if isinstance(fact, dict):
        return fact
    return {}
