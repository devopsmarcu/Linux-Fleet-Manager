from __future__ import annotations

from typing import Any

from core.exceptions import InventoryError
from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import (
    CheckItemResult,
    HealthState,
    Host,
    HostHealthReport,
)

logger = get_logger("health")


class HealthChecker:
    """Serviço responsável por executar health checks e avaliar a saúde dos hosts."""

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

    def run_health_check(
        self,
        targets: str = "all",
        group: str | None = None,
        host_name: str | None = None,
        timeout: int | None = None,
        quick: bool = False,
    ) -> list[HostHealthReport]:
        """Executa o playbook de health check e avalia cada host."""
        target_pattern = host_name or group or targets or "all"
        known_hosts = {h.name: h for h in self.inventory_manager.list_hosts()}

        try:
            expected_hosts = self.inventory_manager.list_hosts(group=group, host_name=host_name)
        except InventoryError:
            expected_hosts = list(known_hosts.values())

        logger.info(
            "Iniciando verificação de saúde (Health Check LFM)",
            extra={"lfm_targets": target_pattern, "lfm_timeout": timeout},
        )

        try:
            # Pass the 'quick' flag as an extra variable to Ansible
            op_result = self.executor.playbook(
                playbook="health_check.yml",
                targets=target_pattern,
                timeout=timeout,
                become=False,
                extra_vars={"lfm_quick_check": quick},
            )
        except Exception as exc:
            logger.error(
                "Falha na execução do playbook de health check",
                extra={"lfm_error": str(exc)},
            )
            op_result = None

        reports: dict[str, HostHealthReport] = {}

        if op_result:
            for task in op_result.task_results:
                h_name = task.host
                host_obj = known_hosts.get(h_name, Host(name=h_name, address=h_name))

                if task.unreachable or task.failed:
                    err_msg = task.msg or task.stderr or "Host inalcançável"
                    if "timeout" in err_msg.lower():
                        issue = "SSH timeout"
                    elif "permission denied" in err_msg.lower():
                        issue = "SSH auth failed"
                    else:
                        issue = "SSH connection failed"

                    reports[h_name] = HostHealthReport(
                        host=h_name,
                        address=host_obj.address,
                        status=HealthState.CRITICAL,
                        issues=[issue],
                        checks=[
                            CheckItemResult(name="Connectivity", passed=False, state=HealthState.CRITICAL, details=err_msg),
                            CheckItemResult(name="SSH", passed=False, state=HealthState.CRITICAL, details=err_msg),
                        ],
                    )
                else:
                    task_data = task.data if isinstance(task.data, dict) else {}
                    raw_facts = task_data.get("ansible_facts")
                    facts = raw_facts if isinstance(raw_facts, dict) else {}
                    health_data = facts.get("lfm_health_data") or task_data.get("lfm_health_data")

                    if health_data and isinstance(health_data, dict):
                        reports[h_name] = self.evaluate_host_facts(h_name, host_obj, health_data)
                    elif h_name not in reports:
                        reports[h_name] = HostHealthReport(
                            host=h_name,
                            address=host_obj.address,
                            status=HealthState.HEALTHY,
                        )

        # Tratar hosts cadastrados que não retornaram na resposta
        for h in expected_hosts:
            if h.name not in reports:
                reports[h.name] = HostHealthReport(
                    host=h.name,
                    address=h.address,
                    status=HealthState.CRITICAL,
                    issues=["SSH timeout"],
                    checks=[
                        CheckItemResult(name="Connectivity", passed=False, state=HealthState.CRITICAL, details="Host sem resposta"),
                        CheckItemResult(name="SSH", passed=False, state=HealthState.CRITICAL, details="Host sem resposta"),
                    ],
                )

        final_list = sorted(reports.values(), key=lambda r: r.host)
        for rep in final_list:
            logger.info(
                "Health check do host avaliado",
                extra={
                    "lfm_host": rep.host,
                    "lfm_status": rep.status.value,
                    "lfm_issues": rep.issues_display,
                },
            )
        return final_list

    @staticmethod
    def evaluate_host_facts(
        host_name: str,
        host_obj: Host,
        health_data: dict[str, Any],
    ) -> HostHealthReport:
        """Avalia 9 verificações de saúde básicas e retorna o relatório consolidado."""
        checks: list[CheckItemResult] = []
        issues: list[str] = []
        worst_state = HealthState.HEALTHY

        def update_worst(state: HealthState) -> None:
            nonlocal worst_state
            if state == HealthState.CRITICAL:
                worst_state = HealthState.CRITICAL
            elif state == HealthState.WARNING and worst_state != HealthState.CRITICAL:
                worst_state = HealthState.WARNING

        # 1. Connectivity & 2. SSH (Se chegou aqui com facts, estão OK)
        checks.append(CheckItemResult(name="Connectivity", passed=True, state=HealthState.HEALTHY))
        checks.append(CheckItemResult(name="SSH", passed=True, state=HealthState.HEALTHY))

        # 3. Disk Check
        mounts = health_data.get("mounts") or []
        root_mount = None
        for m in mounts:
            if isinstance(m, dict) and m.get("mount") == "/":
                root_mount = m
                break
        target_mount = root_mount or (mounts[0] if mounts and isinstance(mounts[0], dict) else None)

        if target_mount:
            size_total = target_mount.get("size_total") or (
                target_mount.get("block_total", 0) * target_mount.get("block_size", 0)
            )
            size_avail = target_mount.get("size_available") or (
                target_mount.get("block_available", 0) * target_mount.get("block_size", 0)
            )
            if size_total and size_total > 0:
                used_pct = round(((size_total - size_avail) / size_total) * 100, 1)
                if used_pct >= 95.0:
                    st = HealthState.CRITICAL
                    msg = f"Disk {used_pct:.0f}%"
                    issues.append(msg)
                    update_worst(st)
                    checks.append(CheckItemResult(name="Disk", passed=False, state=st, details=msg))
                elif used_pct >= 80.0:
                    st = HealthState.WARNING
                    msg = f"Disk {used_pct:.0f}%"
                    issues.append(msg)
                    update_worst(st)
                    checks.append(CheckItemResult(name="Disk", passed=True, state=st, details=msg))
                else:
                    checks.append(CheckItemResult(name="Disk", passed=True, state=HealthState.HEALTHY))
            else:
                checks.append(CheckItemResult(name="Disk", passed=True, state=HealthState.HEALTHY))
        else:
            checks.append(CheckItemResult(name="Disk", passed=True, state=HealthState.HEALTHY))

        # 4. Memory Check
        try:
            mem_total = float(health_data.get("mem_total_mb") or 0)
            mem_avail = float(health_data.get("mem_available_mb") or health_data.get("mem_free_mb") or 0)
            if mem_total > 0:
                used_mem_pct = round(((mem_total - mem_avail) / mem_total) * 100, 1)
                if used_mem_pct >= 95.0:
                    st = HealthState.CRITICAL
                    msg = f"Memory {used_mem_pct:.0f}%"
                    issues.append(msg)
                    update_worst(st)
                    checks.append(CheckItemResult(name="Memory", passed=False, state=st, details=msg))
                elif used_mem_pct >= 85.0:
                    st = HealthState.WARNING
                    msg = f"Memory {used_mem_pct:.0f}%"
                    issues.append(msg)
                    update_worst(st)
                    checks.append(CheckItemResult(name="Memory", passed=True, state=st, details=msg))
                else:
                    checks.append(CheckItemResult(name="Memory", passed=True, state=HealthState.HEALTHY))
            else:
                checks.append(CheckItemResult(name="Memory", passed=True, state=HealthState.HEALTHY))
        except (ValueError, TypeError):
            checks.append(CheckItemResult(name="Memory", passed=True, state=HealthState.HEALTHY))

        # 5. CPU Load Check
        try:
            load_1m = float(health_data.get("load_1m") or 0.0)
            cpu_cores = int(health_data.get("cpu_cores") or 1)
            load_per_core = load_1m / max(1, cpu_cores)
            if load_per_core >= 3.0:
                st = HealthState.CRITICAL
                msg = f"CPU Load {load_1m:.1f}"
                issues.append(msg)
                update_worst(st)
                checks.append(CheckItemResult(name="CPU Load", passed=False, state=st, details=msg))
            elif load_per_core >= 1.5:
                st = HealthState.WARNING
                msg = f"CPU Load {load_1m:.1f}"
                issues.append(msg)
                update_worst(st)
                checks.append(CheckItemResult(name="CPU Load", passed=True, state=st, details=msg))
            else:
                checks.append(CheckItemResult(name="CPU Load", passed=True, state=HealthState.HEALTHY))
        except (ValueError, TypeError):
            checks.append(CheckItemResult(name="CPU Load", passed=True, state=HealthState.HEALTHY))

        # 6. Services Check
        failed_services = health_data.get("failed_services") or []
        if failed_services:
            st = HealthState.WARNING
            msg = f"Service {failed_services[0]} failed"
            issues.append(msg)
            update_worst(st)
            checks.append(CheckItemResult(name="Services", passed=False, state=st, details=msg))
        else:
            checks.append(CheckItemResult(name="Services", passed=True, state=HealthState.HEALTHY))

        # 7. Updates Check
        try:
            pending_updates = int(health_data.get("pending_updates") or 0)
            if pending_updates >= 50:
                st = HealthState.WARNING
                msg = f"{pending_updates} updates"
                issues.append(msg)
                update_worst(st)
                checks.append(CheckItemResult(name="Updates", passed=True, state=st, details=msg))
            else:
                checks.append(CheckItemResult(name="Updates", passed=True, state=HealthState.HEALTHY))
        except (ValueError, TypeError):
            checks.append(CheckItemResult(name="Updates", passed=True, state=HealthState.HEALTHY))

        # 8. DNS Check
        dns_ok = bool(health_data.get("dns_ok", True))
        if not dns_ok:
            st = HealthState.WARNING
            msg = "DNS resolution fail"
            issues.append(msg)
            update_worst(st)
            checks.append(CheckItemResult(name="DNS", passed=False, state=st, details=msg))
        else:
            checks.append(CheckItemResult(name="DNS", passed=True, state=HealthState.HEALTHY))

        # 9. Network Check
        network_ok = bool(health_data.get("network_ok", True))
        if not network_ok:
            st = HealthState.WARNING
            msg = "No network"
            issues.append(msg)
            update_worst(st)
            checks.append(CheckItemResult(name="Network", passed=False, state=st, details=msg))
        else:
            checks.append(CheckItemResult(name="Network", passed=True, state=HealthState.HEALTHY))

        return HostHealthReport(
            host=host_name,
            address=str(health_data.get("default_ip") or host_obj.address),
            status=worst_state,
            issues=issues,
            checks=checks,
            raw_facts=health_data,
        )
