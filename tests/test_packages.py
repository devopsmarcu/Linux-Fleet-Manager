"""tests/test_packages.py — Testes da camada Python de gerenciamento de pacotes.

Estratégia de teste:
- Todos os testes são unitários e não dependem de Ansible real.
- O AnsibleExecutor é substituído por um Mock que retorna AnsibleOperationResult
  com os dados que o teste precisar avaliar.
- Cobertura: validação, detecção de pkg manager, parsing de resultados,
  relatório agregado, fluxo de install/remove, erros parciais.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    PackageAction,
    PackageHostResult,
    PackageReport,
)
from core.packages import (
    PackageManager,
    PackageValidationError,
    _extract_package_fact,
)


# ---------------------------------------------------------------------------
# Helpers de fixture
# ---------------------------------------------------------------------------

def _make_task(
    host: str,
    failed: bool = False,
    unreachable: bool = False,
    changed: bool = False,
    pkg_fact: dict | None = None,
    msg: str | None = None,
    duration: float = 0.1,
) -> AnsibleTaskResult:
    if unreachable:
        state = ChangeState.UNREACHABLE
    elif failed:
        state = ChangeState.FAILED
    elif changed:
        state = ChangeState.CHANGED
    else:
        state = ChangeState.UNCHANGED

    data: dict = {}
    if pkg_fact:
        data["lfm_package_result"] = pkg_fact

    return AnsibleTaskResult(
        host=host,
        task="LFM — Package Management",
        state=state,
        failed=failed,
        unreachable=unreachable,
        changed=changed,
        msg=msg,
        data=data,
        duration_seconds=duration,
    )


def _make_op_result(tasks: list[AnsibleTaskResult], rc: int = 0) -> AnsibleOperationResult:
    op = AnsibleOperationResult(
        operation_id="test-op-01",
        command=["ansible-playbook", "package_manage.yml"],
        return_code=rc,
        task_results=tasks,
        started_at=datetime.now(),
    )
    op.finalize()
    return op


def _make_executor(op_result: AnsibleOperationResult | None = None) -> MagicMock:
    mock = MagicMock()
    if op_result is not None:
        mock.playbook.return_value = op_result
    else:
        mock.playbook.side_effect = Exception("Executor indisponível")
    return mock


def _make_inventory(hosts: list[str]) -> MagicMock:
    mock = MagicMock()
    mock.inventory_file = MagicMock()
    host_mocks = []
    for i, h in enumerate(hosts):
        hm = MagicMock()
        hm.name = h
        hm.address = f"192.168.1.{i + 1}"
        host_mocks.append(hm)
    mock.list_hosts.return_value = host_mocks
    return mock


# ---------------------------------------------------------------------------
# TestPackageValidation
# ---------------------------------------------------------------------------

class TestPackageValidation:
    """Testa a validação estática do nome do pacote."""

    @pytest.mark.parametrize("name", [
        "htop",
        "python3-pip",
        "lib32gcc-s1",
        "linux-image-5.15.0-88-generic",
        "vim.tiny",
        "gcc++",
        "pkg_name",
        "2to3",       # começa com dígito (válido no Linux)
        "1password",  # começa com dígito (válido)
    ])
    def test_valid_package_names(self, name: str) -> None:
        # Não deve levantar exceção
        PackageManager.validate_package_name(name)

    @pytest.mark.parametrize("name", [
        "",
        "  ",
        "pkg name",        # espaço interno
        "pkg;rm -rf /",   # injeção de shell
        "pkg&&bad",        # operador shell
        "pkg|pipe",        # pipe
        "p" * 200,         # muito longo
        "../../etc/passwd", # path traversal
    ])
    def test_invalid_package_names(self, name: str) -> None:
        with pytest.raises(PackageValidationError):
            PackageManager.validate_package_name(name)

    def test_empty_name_raises(self) -> None:
        with pytest.raises(PackageValidationError) as exc_info:
            PackageManager.validate_package_name("")
        assert "vazio" in exc_info.value.message.lower()

    def test_shell_injection_raises(self) -> None:
        with pytest.raises(PackageValidationError):
            PackageManager.validate_package_name("htop; rm -rf /")


# ---------------------------------------------------------------------------
# TestDetectPkgManager
# ---------------------------------------------------------------------------

class TestDetectPkgManager:
    """Testa a detecção estática do package manager pela distribuição."""

    @pytest.mark.parametrize("dist, expected", [
        ("Ubuntu", "apt"),
        ("Debian", "apt"),
        ("ubuntu", "apt"),
        ("Fedora", "dnf"),
        ("RHEL", "dnf"),
        ("CentOS", "dnf"),
        ("Arch Linux", "pacman"),
        ("Manjaro", "pacman"),
        ("openSUSE", "zypper"),
        ("FreeBSD", "pkg"),
        (None, "unknown"),
        ("", "unknown"),
        ("SomethingNew", "unknown"),
    ])
    def test_detect_known_distributions(self, dist: str | None, expected: str) -> None:
        assert PackageManager.detect_pkg_manager(dist) == expected


# ---------------------------------------------------------------------------
# TestExtractPackageFact
# ---------------------------------------------------------------------------

class TestExtractPackageFact:
    """Testa extração do fato lfm_package_result do task.data."""

    def test_extract_from_direct_key(self) -> None:
        data = {
            "lfm_package_result": {
                "pkg_name": "htop",
                "changed": True,
                "pkg_manager": "apt",
            }
        }
        result = _extract_package_fact(data)
        assert result["pkg_name"] == "htop"
        assert result["pkg_manager"] == "apt"

    def test_extract_from_ansible_facts(self) -> None:
        data = {
            "ansible_facts": {
                "lfm_package_result": {
                    "pkg_name": "nginx",
                    "changed": False,
                    "pkg_manager": "apt",
                }
            }
        }
        result = _extract_package_fact(data)
        assert result["pkg_name"] == "nginx"

    def test_empty_data_returns_empty(self) -> None:
        assert _extract_package_fact({}) == {}
        assert _extract_package_fact(None) == {}  # type: ignore[arg-type]

    def test_missing_fact_returns_empty(self) -> None:
        assert _extract_package_fact({"other_key": "value"}) == {}


# ---------------------------------------------------------------------------
# TestParseResults
# ---------------------------------------------------------------------------

class TestParseResults:
    """Testa o parser estático de AnsibleOperationResult → PackageHostResult."""

    def test_all_success_no_change(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=False, pkg_fact={"pkg_manager": "apt"}),
            _make_task("ubuntu-02", changed=False, pkg_fact={"pkg_manager": "apt"}),
        ]
        op = _make_op_result(tasks)
        results = PackageManager._parse_results(op, {}, "htop", PackageAction.INSTALL)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert all(not r.changed for r in results)
        assert results[0].host == "ubuntu-01"
        assert results[1].host == "ubuntu-02"

    def test_install_with_changed(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
        ]
        op = _make_op_result(tasks)
        results = PackageManager._parse_results(op, {}, "htop", PackageAction.INSTALL)

        assert results[0].success is True
        assert results[0].changed is True
        assert results[0].pkg_manager == "apt"

    def test_unreachable_host(self) -> None:
        tasks = [
            _make_task("ubuntu-01", unreachable=True, msg="SSH connection failed"),
        ]
        op = _make_op_result(tasks, rc=4)
        results = PackageManager._parse_results(op, {}, "htop", PackageAction.INSTALL)

        assert results[0].success is False
        assert results[0].error == "SSH connection failed"
        assert results[0].changed is False

    def test_failed_host(self) -> None:
        tasks = [
            _make_task("debian-01", failed=True, msg="Package not found"),
        ]
        op = _make_op_result(tasks, rc=2)
        results = PackageManager._parse_results(op, {}, "bad-pkg", PackageAction.INSTALL)

        assert results[0].success is False
        assert results[0].error is not None

    def test_mixed_results(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("ubuntu-02", changed=False, pkg_fact={"pkg_manager": "apt"}),
            _make_task("debian-01", unreachable=True, msg="Timeout"),
            _make_task("xubuntu-01", failed=True, msg="Permission denied"),
        ]
        op = _make_op_result(tasks, rc=2)
        results = PackageManager._parse_results(op, {}, "nginx", PackageAction.INSTALL)

        success = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(success) == 2
        assert len(failures) == 2

    def test_results_are_sorted_by_hostname(self) -> None:
        tasks = [
            _make_task("zz-host"),
            _make_task("aa-host"),
            _make_task("mm-host"),
        ]
        op = _make_op_result(tasks)
        results = PackageManager._parse_results(op, {}, "htop", PackageAction.INSTALL)
        names = [r.host for r in results]
        assert names == sorted(names)

    def test_duplicate_task_entries_not_duplicated(self) -> None:
        """Host aparece duas vezes nas tasks (validate + package), deve aparecer uma vez."""
        tasks = [
            _make_task("ubuntu-01", changed=False),  # task: validate
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt"}),  # task: package
        ]
        op = _make_op_result(tasks)
        results = PackageManager._parse_results(op, {}, "htop", PackageAction.INSTALL)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# TestPackageReport
# ---------------------------------------------------------------------------

class TestPackageReport:
    """Testa propriedades do relatório agregado."""

    def _make_report(self, results: list[PackageHostResult]) -> PackageReport:
        r = PackageReport(
            package="htop",
            action=PackageAction.INSTALL,
            targets="all",
            results=results,
        )
        r.finalize()
        return r

    def test_counts(self) -> None:
        results = [
            PackageHostResult(host="h1", action=PackageAction.INSTALL, package="htop", success=True, changed=True),
            PackageHostResult(host="h2", action=PackageAction.INSTALL, package="htop", success=True, changed=False),
            PackageHostResult(host="h3", action=PackageAction.INSTALL, package="htop", success=False),
            PackageHostResult(host="h4", action=PackageAction.INSTALL, package="htop", success=False),
        ]
        report = self._make_report(results)
        assert report.total == 4
        assert report.success_count == 2
        assert report.failed_count == 2
        assert report.changed_count == 1

    def test_all_success(self) -> None:
        results = [
            PackageHostResult(host="h1", action=PackageAction.INSTALL, package="htop", success=True, changed=True),
            PackageHostResult(host="h2", action=PackageAction.INSTALL, package="htop", success=True, changed=True),
        ]
        report = self._make_report(results)
        assert report.failed_count == 0
        assert report.success_count == 2

    def test_finalize_sets_duration(self) -> None:
        report = self._make_report([])
        assert report.finished_at is not None
        assert report.duration_seconds >= 0.0

    def test_status_label_property(self) -> None:
        ok = PackageHostResult(host="h1", action=PackageAction.INSTALL, package="htop", success=True)
        fail = PackageHostResult(host="h2", action=PackageAction.INSTALL, package="htop", success=False)
        assert ok.status_label == "OK"
        assert fail.status_label == "FAIL"


# ---------------------------------------------------------------------------
# TestPackageManagerService (integração com mock)
# ---------------------------------------------------------------------------

class TestPackageManagerService:
    """Testa o fluxo completo de PackageManager com executor mockado."""

    def _make_manager(self, op_result: AnsibleOperationResult | None) -> PackageManager:
        executor = _make_executor(op_result)
        inventory = _make_inventory(["ubuntu-01", "ubuntu-02", "debian-01"])
        manager = PackageManager.__new__(PackageManager)
        manager.executor = executor
        manager.inventory_manager = inventory
        return manager

    def test_install_all_success(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("ubuntu-02", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("debian-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
        ]
        op = _make_op_result(tasks)
        manager = self._make_manager(op)

        report = manager.install("htop")
        assert report.package == "htop"
        assert report.action == PackageAction.INSTALL
        assert report.total == 3
        assert report.success_count == 3
        assert report.failed_count == 0
        assert report.changed_count == 3

    def test_remove_success(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
        ]
        op = _make_op_result(tasks)
        manager = self._make_manager(op)

        report = manager.remove("nginx")
        assert report.action == PackageAction.REMOVE
        assert report.success_count == 1

    def test_partial_failure(self) -> None:
        tasks = [
            _make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("ubuntu-02", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("debian-01", changed=True, pkg_fact={"pkg_manager": "apt", "changed": True}),
            _make_task("xubuntu-01", unreachable=True, msg="SSH timeout"),
        ]
        op = _make_op_result(tasks, rc=4)
        manager = self._make_manager(op)

        report = manager.install("htop")
        assert report.success_count == 3
        assert report.failed_count == 1
        failures = [r for r in report.results if not r.success]
        assert failures[0].host == "xubuntu-01"

    def test_executor_exception_marks_all_failed(self) -> None:
        manager = self._make_manager(op_result=None)
        report = manager.install("htop")
        assert report.failed_count > 0
        assert report.success_count == 0

    def test_validation_error_before_executor(self) -> None:
        manager = self._make_manager(op_result=MagicMock())
        with pytest.raises(PackageValidationError):
            manager.install("bad pkg name!")
        # Executor nunca deve ter sido chamado
        manager.executor.playbook.assert_not_called()

    def test_install_already_installed(self) -> None:
        """Pacote já instalado: success=True, changed=False."""
        tasks = [
            _make_task("ubuntu-01", changed=False, pkg_fact={"pkg_manager": "apt", "changed": False}),
        ]
        op = _make_op_result(tasks)
        manager = self._make_manager(op)

        report = manager.install("htop")
        assert report.success_count == 1
        assert report.changed_count == 0

    def test_report_targets_field(self) -> None:
        tasks = [_make_task("ubuntu-01", changed=True, pkg_fact={"pkg_manager": "apt"})]
        op = _make_op_result(tasks)
        manager = self._make_manager(op)

        report = manager._run_operation("vim", PackageAction.INSTALL, host_name="ubuntu-01")
        assert report.targets == "ubuntu-01"

    def test_remove_absent_package(self) -> None:
        """Pacote já ausente: success=True, changed=False."""
        tasks = [
            _make_task("ubuntu-01", changed=False, pkg_fact={"pkg_manager": "apt", "changed": False}),
        ]
        op = _make_op_result(tasks)
        manager = self._make_manager(op)

        report = manager.remove("nginx")
        assert report.success_count == 1
        assert report.changed_count == 0
