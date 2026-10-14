from __future__ import annotations

from ansible import AnsibleExecutor, AnsibleRunner
from core.models import HostStatusEnum


class TestAnsibleExecutor:
    def test_init_accepts_options(self) -> None:
        executor = AnsibleExecutor(forks=15, timeout=60)
        assert executor.forks == 15
        assert executor.timeout == 60
        # verify that the default provider is correctly initialized
        assert executor.inventory_provider.inventory_file.exists()

    def test_alias_ansible_runner(self) -> None:
        assert AnsibleRunner is AnsibleExecutor

    def test_ping_execution(self) -> None:
        executor = AnsibleExecutor(timeout=5)
        # Test ping against localhost / all configured hosts
        results = executor.ping(targets="localhost")
        assert len(results) == 1
        assert results[0].host.name == "localhost"
        assert results[0].status == HostStatusEnum.ONLINE

    def test_ping_unreachable_host_does_not_halt_execution(self, monkeypatch):
        # Mock the adhoc method to avoid real SSH calls and timeouts
        def mock_adhoc(self, module, args=None, targets="all", become=None, extra_vars=None, timeout=None, verification_cmd=None):
            from core.models import AnsibleOperationResult, AnsibleTaskResult, ChangeState
            return AnsibleOperationResult(
                operation_id="mock_op",
                command=[],
                task_results=[
                    AnsibleTaskResult(
                        host="localhost",
                        state=ChangeState.UNCHANGED,
                        changed=False,
                        failed=False,
                        unreachable=False,
                    ),
                    AnsibleTaskResult(
                        host="remote-host",
                        state=ChangeState.UNREACHABLE,
                        unreachable=True,
                        msg="UNREACHABLE! failed to connect to host"
                    ),
                ]
            )

        monkeypatch.setattr(AnsibleExecutor, "adhoc", mock_adhoc)

        executor = AnsibleExecutor(timeout=2)
        results = executor.ping(targets="all")
        # Should return status for all hosts without throwing exceptions
        assert len(results) >= 2
        host_names = [r.host.name for r in results]
        assert "localhost" in host_names
        assert "remote-host" in host_names
        localhost_status = next(r for r in results if r.host.name == "localhost")
        assert localhost_status.status == HostStatusEnum.ONLINE
