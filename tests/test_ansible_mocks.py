from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime

from core.config import Settings
from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    HostStatus,
    HostStatusEnum,
    Host,
)
from ansible.runner import AnsibleExecutor

@pytest.fixture
def mock_executor():
    """Returns a mock of AnsibleExecutor to avoid real SSH calls."""
    with patch("ansible.runner.AnsibleExecutor._run") as mock_run:
        yield mock_run

@pytest.fixture
def sample_host():
    return Host(name="localhost", address="127.0.0.1")

def test_executor_timeout_propagation():
    """Verify that the timeout passed to adhoc is actually used in the command line."""
    with patch("ansible.runner.AnsibleExecutor._run") as mock_run:
        # Mock _run to return a dummy result
        mock_run.return_value = AnsibleOperationResult(
            operation_id="test", command=[], task_results=[]
        )

        executor = AnsibleExecutor()
        executor.adhoc(module="ping", targets="all", timeout=42)

        # Check if the first argument to _run (the cmd list) contains the correct timeout
        called_cmd = mock_run.call_args[0][0]
        assert "-T" in called_cmd
        idx = called_cmd.index("-T")
        assert called_cmd[idx + 1] == "42"

def test_ping_success_mocked():
    """Test ping logic with mocked results."""
    with patch("ansible.runner.AnsibleExecutor._run") as mock_run:
        # Mock an Ansible JSON output for ping
        mock_run.return_value = AnsibleOperationResult(
            operation_id="op123",
            command=["ansible", "all", "-m", "ping"],
            task_results=[
                AnsibleTaskResult(
                    host="localhost",
                    state=ChangeState.UNCHANGED,
                    changed=False,
                    failed=False,
                    unreachable=False,
                    data={"ping": "pong", "delta": 0.01}
                )
            ]
        )

        executor = AnsibleExecutor()
        results = executor.ping(targets="localhost")

        assert len(results) == 1
        assert results[0].host.name == "localhost"
        assert results[0].status == HostStatusEnum.ONLINE
        assert results[0].latency_ms == 10

def test_ping_unreachable_mocked():
    """Test ping with an unreachable host."""
    with patch("ansible.runner.AnsibleExecutor._run") as mock_run:
        mock_run.return_value = AnsibleOperationResult(
            operation_id="op123",
            command=["ansible", "all", "-m", "ping"],
            task_results=[
                AnsibleTaskResult(
                    host="remote-host",
                    state=ChangeState.UNREACHABLE,
                    unreachable=True,
                    msg="UNREACHABLE! failed to connect to host"
                )
            ]
        )

        executor = AnsibleExecutor()
        results = executor.ping(targets="remote-host")

        assert len(results) == 1
        assert results[0].status == HostStatusEnum.OFFLINE
        assert "unreachable" in results[0].error_message.lower()
