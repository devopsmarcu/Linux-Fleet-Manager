from __future__ import annotations

from ansible import AnsibleExecutor, AnsibleRunner
from core.models import HostStatusEnum


class TestAnsibleExecutor:
    def test_init_accepts_options(self) -> None:
        executor = AnsibleExecutor(forks=15, timeout=60)
        assert executor.forks == 15
        assert executor.timeout == 60
        assert executor.inventory_file.exists()

    def test_alias_ansible_runner(self) -> None:
        assert AnsibleRunner is AnsibleExecutor

    def test_ping_execution(self) -> None:
        executor = AnsibleExecutor(timeout=5)
        # Test ping against localhost / all configured hosts
        results = executor.ping(targets="localhost")
        assert len(results) == 1
        assert results[0].host.name == "localhost"
        assert results[0].status == HostStatusEnum.ONLINE

    def test_ping_unreachable_host_does_not_halt_execution(self) -> None:
        executor = AnsibleExecutor(timeout=2)
        results = executor.ping(targets="all")
        # Should return status for all hosts without throwing exceptions
        assert len(results) >= 1
        host_names = [r.host.name for r in results]
        assert "localhost" in host_names
        localhost_status = next(r for r in results if r.host.name == "localhost")
        assert localhost_status.status == HostStatusEnum.ONLINE
