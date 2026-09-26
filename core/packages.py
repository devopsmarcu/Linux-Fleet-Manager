"""core/packages.py — Serviço de gerenciamento de pacotes do LFM.

Responsabilidades:
- Validar nome de pacote antes de qualquer execução remota.
- Resolver o conjunto de hosts-alvo via InventoryManager.
- Executar o playbook package_manage.yml via AnsibleExecutor.
- Parsear o resultado por host e construir PackageReport.
- Logar cada operação sem misturar apresentação com lógica.
"""
from __future__ import annotations

import re
from typing import Any

from core.exceptions import LFMError
from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import (
    AnsibleOperationResult,
    Host,
    PackageAction,
    PackageHostResult,
    PackageReport,
)

logger = get_logger("packages")

# Padrão conservador: só permite nomes de pacotes seguros.
# Suporta: htop, python3-pip, lib32gcc-s1, linux-image-5.15.0-88-generic, etc.
_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+\-]{0,127}$")

# Package managers suportados no momento (apt/apt-get via ansible.builtin.package)
_SUPPORTED_PKG_MANAGERS = {"apt", "apt-get", "dnf", "yum", "pacman", "zypper", "pkg"}


class PackageValidationError(LFMError):
    """Raised when the package name fails validation."""


class PackageManager:
    """Serviço que orquestra install/remove de pacotes em múltiplos hosts Linux.

    Usa `ansible.builtin.package` (módulo genérico), que detecta automaticamente
    o package manager de cada host via facts (apt, dnf, yum, pacman...).
    Nunca usa shell diretamente.
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

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def install(
        self,
        package: str,
        host_name: str | None = None,
        group: str | None = None,
        timeout: int | None = None,
    ) -> PackageReport:
        """Instala *package* nos hosts indicados."""
        return self._run_operation(
            package=package,
            action=PackageAction.INSTALL,
            host_name=host_name,
            group=group,
            timeout=timeout,
        )

    def remove(
        self,
        package: str,
        host_name: str | None = None,
        group: str | None = None,
        timeout: int | None = None,
    ) -> PackageReport:
        """Remove *package* dos hosts indicados."""
        return self._run_operation(
            package=package,
            action=PackageAction.REMOVE,
            host_name=host_name,
            group=group,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Lógica interna
    # ------------------------------------------------------------------

    def _run_operation(
        self,
        package: str,
        action: PackageAction,
        host_name: str | None = None,
        group: str | None = None,
        timeout: int | None = None,
    ) -> PackageReport:
        """Valida, resolve targets, executa playbook e constrói PackageReport."""
        self.validate_package_name(package)

        target_pattern = host_name or group or "all"
        known_hosts = {h.name: h for h in self.inventory_manager.list_hosts()}
        pkg_state = "present" if action == PackageAction.INSTALL else "absent"

        logger.info(
            "Iniciando operação de pacote",
            extra={
                "lfm_action": action.value,
                "lfm_package": package,
                "lfm_targets": target_pattern,
            },
        )

        report = PackageReport(
            package=package,
            action=action,
            targets=target_pattern,
        )

        try:
            op_result: AnsibleOperationResult | None = self.executor.playbook(
                playbook="package_manage.yml",
                targets=target_pattern,
                become=True,
                extra_vars={
                    "pkg_name": package,
                    "pkg_state": pkg_state,
                    "lfm_targets": target_pattern,
                },
                timeout=timeout,
            )
        except Exception as exc:
            logger.error(
                "Falha na execução do playbook de pacotes",
                extra={"lfm_error": str(exc)},
            )
            op_result = None

        if op_result:
            report.results = self._parse_results(
                op_result, known_hosts, package, action
            )
        else:
            # Falha total da execução: marcar todos os hosts esperados como FAIL
            try:
                expected = self.inventory_manager.list_hosts(
                    group=group, host_name=host_name
                )
            except Exception:
                expected = list(known_hosts.values())

            for h in expected:
                report.results.append(
                    PackageHostResult(
                        host=h.name,
                        action=action,
                        package=package,
                        success=False,
                        error="Executor não disponível ou falha de conexão",
                    )
                )

        report.finalize()

        for r in report.results:
            logger.info(
                "Resultado da operação de pacote",
                extra={
                    "lfm_host": r.host,
                    "lfm_action": r.action.value,
                    "lfm_package": r.package,
                    "lfm_success": r.success,
                    "lfm_changed": r.changed,
                    "lfm_pkg_manager": r.pkg_manager,
                },
            )

        return report

    @staticmethod
    def _parse_results(
        op_result: AnsibleOperationResult,
        known_hosts: dict[str, Host],
        package: str,
        action: PackageAction,
    ) -> list[PackageHostResult]:
        """Converte AnsibleTaskResult → PackageHostResult para cada host."""
        results: list[PackageHostResult] = []
        seen: set[str] = set()

        for task in op_result.task_results:
            h_name = task.host
            # Evitar duplicar entradas (um host pode aparecer em múltiplas tasks)
            if h_name in seen:
                # Atualizar resultado existente com status do último step se relevante
                for r in results:
                    if r.host == h_name and (task.failed or task.unreachable):
                        r.success = False
                        r.error = task.msg or task.stderr or "Falha na tarefa"
                continue
            seen.add(h_name)

            if task.unreachable or task.failed:
                err_msg = task.msg or task.stderr or "Falha na execução"
                results.append(
                    PackageHostResult(
                        host=h_name,
                        action=action,
                        package=package,
                        success=False,
                        changed=False,
                        error=err_msg,
                        duration_seconds=task.duration_seconds,
                    )
                )
            else:
                # Extrair dados do fato exportado pelo playbook
                pkg_data = _extract_package_fact(task.data)
                results.append(
                    PackageHostResult(
                        host=h_name,
                        action=action,
                        package=package,
                        success=True,
                        changed=pkg_data.get("changed", task.changed),
                        pkg_manager=pkg_data.get("pkg_manager"),
                        distribution=pkg_data.get("distribution"),
                        msg=pkg_data.get("msg") or task.msg,
                        duration_seconds=task.duration_seconds,
                    )
                )

        return sorted(results, key=lambda r: r.host)

    # ------------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------------

    @staticmethod
    def validate_package_name(name: str) -> None:
        """Levanta PackageValidationError se o nome do pacote for inválido."""
        if not name or not name.strip():
            raise PackageValidationError(
                "Nome do pacote não pode ser vazio.", package=name
            )
        clean = name.strip()
        if not _PACKAGE_NAME_RE.match(clean):
            raise PackageValidationError(
                "Nome de pacote inválido. Use apenas letras, números, '.', '+', '-', '_'.",
                package=name,
            )

    @staticmethod
    def detect_pkg_manager(distribution: str | None) -> str:
        """Detecta o package manager provável com base na distribuição.

        Usado apenas para fins informativos na UI. A detecção real
        é feita pelo ansible.builtin.package via ansible_pkg_mgr fact.
        """
        if not distribution:
            return "unknown"
        dist_lower = distribution.lower()
        if "ubuntu" in dist_lower or "debian" in dist_lower:
            return "apt"
        if "fedora" in dist_lower or "rhel" in dist_lower or "centos" in dist_lower:
            return "dnf"
        if "arch" in dist_lower or "manjaro" in dist_lower:
            return "pacman"
        if "opensuse" in dist_lower or "suse" in dist_lower:
            return "zypper"
        if "freebsd" in dist_lower:
            return "pkg"
        return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_package_fact(task_data: dict[str, Any]) -> dict[str, Any]:
    """Extrai o dict lfm_package_result injetado pelo playbook via set_fact."""
    if not task_data:
        return {}
    # Pode vir dentro de ansible_facts ou diretamente
    facts = task_data.get("ansible_facts") or {}
    pkg_fact = facts.get("lfm_package_result") or task_data.get("lfm_package_result")
    if isinstance(pkg_fact, dict):
        return pkg_fact
    return {}
