"""tests/test_update_maintenance.py — Testes das camadas Python de update e manutenção.

Estratégia:
- Todos os testes são unitários; AnsibleExecutor é substituído por Mock.
- Cobertura: parsing de resultados por host, relatório agregado,
  avaliação de limiares de saúde, tratamento de falhas parciais.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    MaintenanceCheckState,
    MaintenanceHostReport,
    MaintenanceReport,
    UpdateHostResult,
    UpdateReport,
    _bytes_human,
)
from core.updater import SystemUpdater, _extract_update_fact
from core.maintenance import MaintenanceRunner, _extract_maintenance_fact


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------

def _make_task(
    host: str,
    failed: bool = False,
    unreachable: bool = False,
    changed: bool = False,
    data: dict | None = None,
    msg: str | None = None,
    duration: float = 0.5,
) -> AnsibleTaskResult:
    if unreachable:
        state = ChangeState.UNREACHABLE
    elif failed:
        state = ChangeState.FAILED
    elif changed:
        state = ChangeState.CHANGED
    else:
        state = ChangeState.UNCHANGED

    return AnsibleTaskResult(
        host=host,
        task="LFM Task",
        state=state,
        failed=failed,
        unreachable=unreachable,
        changed=changed,
        msg=msg,
        data=data or {},
        duration_seconds=duration,
    )


def _make_op(tasks: list[AnsibleTaskResult], rc: int = 0) -> AnsibleOperationResult:
    op = AnsibleOperationResult(
        operation_id="test-op",
        command=["ansible-playbook"],
        return_code=rc,
        task_results=tasks,
        started_at=datetime.now(),
    )
    op.finalize()
    return op


def _make_inventory(hosts: list[str]) -> MagicMock:
    inv = MagicMock()
    inv.inventory_file = MagicMock()
    host_mocks = []
    for i, h in enumerate(hosts):
        hm = MagicMock()
        hm.name = h
        hm.address = f"192.168.0.{i + 1}"
        host_mocks.append(hm)
    inv.list_hosts.return_value = host_mocks
    return inv


def _make_executor(op: AnsibleOperationResult | None) -> MagicMock:
    m = MagicMock()
    if op is not None:
        m.playbook.return_value = op
    else:
        m.playbook.side_effect = Exception("indisponível")
    return m


def _make_updater(op: AnsibleOperationResult | None) -> SystemUpdater:
    upd = SystemUpdater.__new__(SystemUpdater)
    upd.inventory_manager = _make_inventory(["ubuntu-01", "ubuntu-02", "debian-01"])
    upd.executor = _make_executor(op)
    return upd


def _make_runner(op: AnsibleOperationResult | None) -> MaintenanceRunner:
    runner = MaintenanceRunner.__new__(MaintenanceRunner)
    runner.inventory_manager = _make_inventory(["ubuntu-01", "ubuntu-02"])
    runner.executor = _make_executor(op)
    return runner


# ---------------------------------------------------------------------------
# TestBytesHuman
# ---------------------------------------------------------------------------

class TestBytesHuman:
    @pytest.mark.parametrize("n, expected", [
        (0, "0 B"),
        (-1, "0 B"),
        (512, "512.0 B"),
        (1024, "1.0 KB"),
        (1536, "1.0 KB"),
        (1_048_576, "1.0 MB"),
        (1_073_741_824, "1.0 GB"),
        (2_147_483_648, "2.0 GB"),
    ])
    def test_human_readable(self, n: int, expected: str) -> None:
        assert _bytes_human(n) == expected


# ---------------------------------------------------------------------------
# TestExtractUpdateFact
# ---------------------------------------------------------------------------

class TestExtractUpdateFact:
    def test_extract_direct(self) -> None:
        data = {"lfm_update_result": {"changed": True, "pkg_manager": "apt"}}
        assert _extract_update_fact(data)["pkg_manager"] == "apt"

    def test_extract_from_ansible_facts(self) -> None:
        data = {"ansible_facts": {"lfm_update_result": {"changed": False, "pkg_manager": "dnf"}}}
        assert _extract_update_fact(data)["pkg_manager"] == "dnf"

    def test_empty_returns_empty(self) -> None:
        assert _extract_update_fact({}) == {}
        assert _extract_update_fact(None) == {}  # type: ignore[arg-type]

    def test_missing_fact_returns_empty(self) -> None:
        assert _extract_update_fact({"other": "value"}) == {}


# ---------------------------------------------------------------------------
# TestExtractMaintenanceFact
# ---------------------------------------------------------------------------

class TestExtractMaintenanceFact:
    def test_extract_direct(self) -> None:
        data = {"lfm_maintenance_data": {"pending_updates": 5, "distribution": "Ubuntu"}}
        result = _extract_maintenance_fact(data)
        assert result["distribution"] == "Ubuntu"

    def test_extract_from_ansible_facts(self) -> None:
        data = {"ansible_facts": {"lfm_maintenance_data": {"pending_updates": 10}}}
        result = _extract_maintenance_fact(data)
        assert result["pending_updates"] == 10

    def test_empty_returns_empty(self) -> None:
        assert _extract_maintenance_fact({}) == {}


# ---------------------------------------------------------------------------
# TestUpdateParseResults
# ---------------------------------------------------------------------------

class TestUpdateParseResults:
    def _op_with_update_fact(self, host: str, changed: bool, pkg_mgr: str) -> AnsibleTaskResult:
        return _make_task(
            host=host,
            changed=changed,
            data={"lfm_update_result": {"changed": changed, "pkg_manager": pkg_mgr, "packages_updated": 5 if changed else 0}},
        )

    def test_all_success_no_change(self) -> None:
        tasks = [
            self._op_with_update_fact("ubuntu-01", False, "apt"),
            self._op_with_update_fact("ubuntu-02", False, "apt"),
        ]
        op = _make_op(tasks)
        results = SystemUpdater._parse_results(op, {})
        assert len(results) == 2
        assert all(r.success for r in results)
        assert all(not r.changed for r in results)

    def test_update_with_changes(self) -> None:
        tasks = [self._op_with_update_fact("ubuntu-01", True, "apt")]
        op = _make_op(tasks)
        results = SystemUpdater._parse_results(op, {})
        assert results[0].changed is True
        assert results[0].packages_updated == 5
        assert results[0].pkg_manager == "apt"

    def test_unreachable_host(self) -> None:
        tasks = [_make_task("ubuntu-01", unreachable=True, msg="SSH timeout")]
        op = _make_op(tasks, rc=4)
        results = SystemUpdater._parse_results(op, {})
        assert results[0].success is False
        assert "timeout" in (results[0].error or "").lower()

    def test_failed_host(self) -> None:
        tasks = [_make_task("debian-01", failed=True, msg="apt lock held")]
        op = _make_op(tasks, rc=2)
        results = SystemUpdater._parse_results(op, {})
        assert results[0].success is False

    def test_mixed_results(self) -> None:
        tasks = [
            self._op_with_update_fact("ubuntu-01", True, "apt"),
            _make_task("ubuntu-02", unreachable=True, msg="timeout"),
            self._op_with_update_fact("debian-01", False, "apt"),
        ]
        op = _make_op(tasks, rc=4)
        results = SystemUpdater._parse_results(op, {})
        ok = [r for r in results if r.success]
        fail = [r for r in results if not r.success]
        assert len(ok) == 2
        assert len(fail) == 1

    def test_sorted_by_hostname(self) -> None:
        tasks = [
            self._op_with_update_fact("zzz-host", False, "apt"),
            self._op_with_update_fact("aaa-host", False, "apt"),
        ]
        op = _make_op(tasks)
        results = SystemUpdater._parse_results(op, {})
        names = [r.host for r in results]
        assert names == sorted(names)

    def test_duplicate_tasks_single_entry(self) -> None:
        """Múltiplas tasks no mesmo host (ex: setup + update) → uma entrada."""
        tasks = [
            _make_task("ubuntu-01", changed=False),  # gather_facts
            _make_task("ubuntu-01", changed=True, data={"lfm_update_result": {"pkg_manager": "apt", "changed": True}}),
        ]
        op = _make_op(tasks)
        results = SystemUpdater._parse_results(op, {})
        assert len(results) == 1
        assert results[0].changed is True


# ---------------------------------------------------------------------------
# TestUpdateReport
# ---------------------------------------------------------------------------

class TestUpdateReport:
    def _report(self, results: list[UpdateHostResult]) -> UpdateReport:
        r = UpdateReport(targets="all", results=results)
        r.finalize()
        return r

    def test_counts(self) -> None:
        results = [
            UpdateHostResult(host="h1", success=True, changed=True, packages_updated=5),
            UpdateHostResult(host="h2", success=True, changed=False, packages_updated=0),
            UpdateHostResult(host="h3", success=False),
        ]
        r = self._report(results)
        assert r.total == 3
        assert r.success_count == 2
        assert r.failed_count == 1
        assert r.changed_count == 1
        assert r.total_packages_updated == 5

    def test_finalize_duration(self) -> None:
        r = self._report([])
        assert r.finished_at is not None
        assert r.duration_seconds >= 0.0

    def test_status_label(self) -> None:
        ok = UpdateHostResult(host="h1", success=True)
        fail = UpdateHostResult(host="h2", success=False)
        assert ok.status_label == "OK"
        assert fail.status_label == "FAIL"


# ---------------------------------------------------------------------------
# TestSystemUpdaterService
# ---------------------------------------------------------------------------

class TestSystemUpdaterService:
    def test_all_updated(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True,
                       data={"lfm_update_result": {"changed": True, "pkg_manager": "apt", "packages_updated": 8}}),
            _make_task("ubuntu-02", changed=True,
                       data={"lfm_update_result": {"changed": True, "pkg_manager": "apt", "packages_updated": 3}}),
        ]
        upd = _make_updater(_make_op(tasks))
        report = upd.run_update()
        assert report.success_count == 2
        assert report.failed_count == 0
        assert report.total_packages_updated == 11

    def test_executor_failure_marks_all_failed(self) -> None:
        upd = _make_updater(None)
        report = upd.run_update()
        assert report.failed_count > 0
        assert report.success_count == 0

    def test_partial_failure(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True,
                       data={"lfm_update_result": {"changed": True, "pkg_manager": "apt"}}),
            _make_task("ubuntu-02", unreachable=True, msg="SSH timeout"),
        ]
        upd = _make_updater(_make_op(tasks, rc=4))
        report = upd.run_update()
        assert report.success_count == 1
        assert report.failed_count == 1

    def test_already_up_to_date(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=False,
                       data={"lfm_update_result": {"changed": False, "pkg_manager": "apt", "packages_updated": 0}}),
        ]
        upd = _make_updater(_make_op(tasks))
        report = upd.run_update()
        assert report.success_count == 1
        assert report.changed_count == 0
        assert report.total_packages_updated == 0


# ---------------------------------------------------------------------------
# TestMaintenanceEvaluate
# ---------------------------------------------------------------------------

class TestMaintenanceEvaluate:
    """Testa evaluate_host_data com diferentes cenários de saúde."""

    _BASE_DATA = {
        "default_ip": "192.168.1.10",
        "distribution": "Ubuntu",
        "mounts": [{"mount": "/", "size_total": 100_000_000_000, "size_available": 60_000_000_000}],
        "mem_total_mb": 8192,
        "mem_available_mb": 4096,
        "load_1m": 0.5,
        "cpu_cores": 4,
        "failed_services": [],
        "active_services": 15,
        "pending_updates": 5,
        "cache_bytes": 200_000_000,   # 200 MB
        "tmp_bytes": 50_000_000,       # 50 MB
        "orphan_packages": 0,
    }

    def _host(self) -> "MagicMock":
        h = MagicMock()
        h.name = "ubuntu-01"
        h.address = "192.168.1.10"
        return h

    def _data(self, **overrides: object) -> dict:
        return {**self._BASE_DATA, **overrides}

    def test_healthy_host(self) -> None:
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), self._data())
        assert rep.overall_state == MaintenanceCheckState.OK
        assert rep.distribution == "Ubuntu"
        assert rep.pending_updates == 5
        check_names = [c.name for c in rep.checks]
        assert "SSH" in check_names
        assert "Disk" in check_names
        assert "Memory" in check_names
        assert "Services" in check_names
        assert "Updates" in check_names
        assert "Cache" in check_names
        assert "Temp" in check_names

    def test_disk_warning(self) -> None:
        # 82% usado → WARNING
        data = self._data(
            mounts=[{"mount": "/", "size_total": 100_000_000_000, "size_available": 18_000_000_000}]
        )
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert rep.overall_state == MaintenanceCheckState.WARNING
        disk_check = next(c for c in rep.checks if c.name == "Disk")
        assert disk_check.state == MaintenanceCheckState.WARNING

    def test_disk_critical(self) -> None:
        # 97% usado → CRITICAL
        data = self._data(
            mounts=[{"mount": "/", "size_total": 100_000_000_000, "size_available": 3_000_000_000}]
        )
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert rep.overall_state == MaintenanceCheckState.CRITICAL
        disk_check = next(c for c in rep.checks if c.name == "Disk")
        assert disk_check.state == MaintenanceCheckState.CRITICAL

    def test_memory_warning(self) -> None:
        # 90% usado → WARNING
        data = self._data(mem_total_mb=8192, mem_available_mb=819)  # ~90%
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert rep.overall_state == MaintenanceCheckState.WARNING

    def test_memory_critical(self) -> None:
        # 97% usado → CRITICAL
        data = self._data(mem_total_mb=8192, mem_available_mb=245)  # ~97%
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert rep.overall_state == MaintenanceCheckState.CRITICAL

    def test_failed_services_warning(self) -> None:
        data = self._data(failed_services=["nginx.service", "cron.service"])
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        svc_check = next(c for c in rep.checks if c.name == "Services")
        assert svc_check.state == MaintenanceCheckState.WARNING
        assert rep.failed_services == ["nginx.service", "cron.service"]

    def test_many_pending_updates_warning(self) -> None:
        data = self._data(pending_updates=25)  # >= 20 → WARNING
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        upd_check = next(c for c in rep.checks if c.name == "Updates")
        assert upd_check.state == MaintenanceCheckState.WARNING
        assert rep.pending_updates == 25

    def test_large_cache_warning(self) -> None:
        data = self._data(cache_bytes=2 * 1024 ** 3)  # 2 GB → WARNING
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        cache_check = next(c for c in rep.checks if c.name == "Cache")
        assert cache_check.state == MaintenanceCheckState.WARNING

    def test_cache_display_human_readable(self) -> None:
        data = self._data(cache_bytes=1_200_000_000)  # ~1.1 GB
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert "GB" in rep.cache_display or "MB" in rep.cache_display

    def test_worst_state_propagates(self) -> None:
        """CRITICAL supera WARNING como estado final."""
        data = self._data(
            mounts=[{"mount": "/", "size_total": 100_000_000_000, "size_available": 3_000_000_000}],  # CRITICAL
            mem_total_mb=8192, mem_available_mb=819,  # WARNING
        )
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        assert rep.overall_state == MaintenanceCheckState.CRITICAL

    def test_empty_mounts_skipped(self) -> None:
        data = self._data(mounts=[])
        rep = MaintenanceRunner.evaluate_host_data("ubuntu-01", self._host(), data)
        disk_check = next(c for c in rep.checks if c.name == "Disk")
        assert disk_check.state == MaintenanceCheckState.SKIPPED


# ---------------------------------------------------------------------------
# TestMaintenanceReport
# ---------------------------------------------------------------------------

class TestMaintenanceReport:
    def _report(self, host_reports: list[MaintenanceHostReport]) -> MaintenanceReport:
        r = MaintenanceReport(targets="all", host_reports=host_reports)
        r.finalize()
        return r

    def test_counts(self) -> None:
        reports = [
            MaintenanceHostReport(host="h1", overall_state=MaintenanceCheckState.OK),
            MaintenanceHostReport(host="h2", overall_state=MaintenanceCheckState.WARNING),
            MaintenanceHostReport(host="h3", overall_state=MaintenanceCheckState.CRITICAL),
            MaintenanceHostReport(host="h4", overall_state=MaintenanceCheckState.OK),
        ]
        r = self._report(reports)
        assert r.total == 4
        assert r.healthy_count == 2
        assert r.warning_count == 1
        assert r.critical_count == 1

    def test_finalize_duration(self) -> None:
        r = self._report([])
        assert r.finished_at is not None
        assert r.duration_seconds >= 0.0

    def test_clean_flag(self) -> None:
        r = MaintenanceReport(targets="all", clean_requested=True)
        assert r.clean_requested is True


# ---------------------------------------------------------------------------
# TestMaintenanceRunnerService
# ---------------------------------------------------------------------------

class TestMaintenanceRunnerService:
    def test_successful_run(self) -> None:
        maint_data = {
            "lfm_maintenance_data": {
                "default_ip": "192.168.1.1",
                "distribution": "Ubuntu",
                "mounts": [{"mount": "/", "size_total": 100_000_000_000, "size_available": 50_000_000_000}],
                "mem_total_mb": 8192,
                "mem_available_mb": 4096,
                "load_1m": 0.3,
                "cpu_cores": 4,
                "failed_services": [],
                "active_services": 20,
                "pending_updates": 3,
                "cache_bytes": 100_000_000,
                "tmp_bytes": 20_000_000,
                "orphan_packages": 0,
            }
        }
        tasks = [_make_task("ubuntu-01", data=maint_data)]
        runner = _make_runner(_make_op(tasks))
        report = runner.run_maintenance()
        assert len(report.host_reports) == 1
        assert report.host_reports[0].overall_state == MaintenanceCheckState.OK
        assert report.host_reports[0].distribution == "Ubuntu"

    def test_unreachable_host(self) -> None:
        tasks = [_make_task("ubuntu-01", unreachable=True, msg="Connection refused")]
        runner = _make_runner(_make_op(tasks, rc=4))
        report = runner.run_maintenance()
        assert report.host_reports[0].overall_state == MaintenanceCheckState.CRITICAL

    def test_executor_failure(self) -> None:
        runner = _make_runner(None)
        report = runner.run_maintenance()
        assert report.critical_count > 0

    def test_sorted_by_hostname(self) -> None:
        maint_data = lambda ip: {
            "lfm_maintenance_data": {
                "default_ip": ip, "distribution": "Ubuntu",
                "mounts": [], "mem_total_mb": 8192, "mem_available_mb": 4096,
                "failed_services": [], "active_services": 5,
                "pending_updates": 0, "cache_bytes": 0, "tmp_bytes": 0,
            }
        }
        tasks = [
            _make_task("zzz-host", data=maint_data("192.168.1.3")),
            _make_task("aaa-host", data=maint_data("192.168.1.1")),
        ]
        runner = _make_runner(_make_op(tasks))
        report = runner.run_maintenance()
        names = [r.host for r in report.host_reports]
        assert names == sorted(names)
