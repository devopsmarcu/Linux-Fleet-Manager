"""core/updater.py — Serviço de atualização de sistema do LFM.

Responsabilidades:
- Executar o playbook update.yml via AnsibleExecutor.
- Parsear resultado por host e construir UpdateReport.
- Logar operação completa sem misturar apresentação com lógica.
"""
from __future__ import annotations

from typing import Any

from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import (
    AnsibleOperationResult,
    Host,
    UpdateHostResult,
    UpdateReport,
)

logger = get_logger("updater")


class SystemUpdater:
    """Serviço que orquestra a atualização de sistemas Linux.

    Usa módulos nativos do Ansible (apt, dnf, pacman) via playbook.
    A operação é idempotente: aplica apenas o que for necessário.
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

    def run_update(
        self,
        host_name: str | None = None,
        group: str | None = None,
        timeout: int | None = None,
    ) -> UpdateReport:
        """Executa atualização de sistema e retorna relatório estruturado."""
        target_pattern = host_name or group or "all"
        known_hosts = {h.name: h for h in self.inventory_manager.list_hosts()}

        try:
            expected_hosts = self.inventory_manager.list_hosts(
                group=group, host_name=host_name
            )
        except Exception:
            expected_hosts = list(known_hosts.values())

        logger.info(
            "Iniciando atualização de sistema",
            extra={"lfm_targets": target_pattern, "lfm_timeout": timeout},
        )

        report = UpdateReport(targets=target_pattern)

        try:
            op_result: AnsibleOperationResult | None = self.executor.playbook(
                playbook="update.yml",
                targets=target_pattern,
                become=True,
                extra_vars={"lfm_targets": target_pattern},
                timeout=timeout,
            )
        except Exception as exc:
            logger.error(
                "Falha na execução do playbook de update",
                extra={"lfm_error": str(exc)},
            )
            op_result = None

        if op_result:
            report.results = self._parse_results(op_result, known_hosts)
        else:
            for h in expected_hosts:
                report.results.append(
                    UpdateHostResult(
                        host=h.name,
                        success=False,
                        error="Executor não disponível ou falha de conexão",
                    )
                )

        report.finalize()

        for r in report.results:
            logger.info(
                "Resultado de atualização por host",
                extra={
                    "lfm_host": r.host,
                    "lfm_success": r.success,
                    "lfm_changed": r.changed,
                    "lfm_packages_updated": r.packages_updated,
                    "lfm_pkg_manager": r.pkg_manager,
                },
            )

        return report

    @staticmethod
    def _parse_results(
        op_result: AnsibleOperationResult,
        known_hosts: dict[str, Host],
    ) -> list[UpdateHostResult]:
        """Converte AnsibleTaskResult → UpdateHostResult por host."""
        results: dict[str, UpdateHostResult] = {}

        for task in op_result.task_results:
            h_name = task.host

            if h_name in results:
                # Atualizar com falha se uma task subsequente falhar
                if task.failed or task.unreachable:
                    results[h_name].success = False
                    results[h_name].error = task.msg or task.stderr or "Falha na tarefa"
                elif task.changed:
                    results[h_name].changed = True
                continue

            if task.unreachable or task.failed:
                results[h_name] = UpdateHostResult(
                    host=h_name,
                    success=False,
                    changed=False,
                    error=task.msg or task.stderr or "Host inalcançável ou falha",
                    duration_seconds=task.duration_seconds,
                )
            else:
                update_data = _extract_update_fact(task.data)
                results[h_name] = UpdateHostResult(
                    host=h_name,
                    success=True,
                    changed=update_data.get("changed", task.changed),
                    packages_updated=int(update_data.get("packages_updated", 0) or 0),
                    pkg_manager=update_data.get("pkg_manager"),
                    distribution=update_data.get("distribution"),
                    duration_seconds=task.duration_seconds,
                )

        return sorted(results.values(), key=lambda r: r.host)


def _extract_update_fact(task_data: dict[str, Any]) -> dict[str, Any]:
    """Extrai lfm_update_result do task.data (direto ou via ansible_facts)."""
    if not task_data:
        return {}
    facts = task_data.get("ansible_facts") or {}
    fact = facts.get("lfm_update_result") or task_data.get("lfm_update_result")
    if isinstance(fact, dict):
        return fact
    return {}
