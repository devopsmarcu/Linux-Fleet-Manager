from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from core.models import (
    AnsibleOperationResult,
    AnsibleTaskResult,
    ChangeState,
    VerificationStatus
)
from ansible.runner import AnsibleExecutor

@pytest.mark.asyncio
async def test_verify_state_success():
    # Scenario: Action OK -> Verify OK -> VERIFIED
    executor = AnsibleExecutor()

    # Setup result with one successful host
    task = AnsibleTaskResult(host="host1", state=ChangeState.CHANGED, failed=False, unreachable=False)
    result = AnsibleOperationResult(
        operation_id="op1",
        command=["ansible", "host1", "-m", "shell", "-a", "echo 1"],
        task_results=[task]
    )

    # Mock the verification subprocess to return RC 0
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"OK", b"")
        mock_proc.returncode = 0
        with patch("asyncio.wait_for", return_value=(b"OK", b"")):
            mock_exec.return_value = mock_proc

            with patch.object(AnsibleExecutor, "_parse_adhoc_line_by_line") as mock_parse:
                mock_parse.return_value = [AnsibleTaskResult(host="host1", state=ChangeState.UNCHANGED, return_code=0)]
                await executor._verify_state(result, "true")

    assert task.verification_status == VerificationStatus.VERIFIED

@pytest.mark.asyncio
async def test_verify_state_fail():
    # Scenario: Action OK -> Verify FAIL -> VERIFICATION_FAILED
    executor = AnsibleExecutor()
    task = AnsibleTaskResult(host="host1", state=ChangeState.CHANGED, failed=False, unreachable=False)
    result = AnsibleOperationResult(
        operation_id="op2",
        command=["ansible", "host1", "-m", "shell", "-a", "echo 1"],
        task_results=[task]
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"Error", b"")
        mock_proc.returncode = 1
        with patch("asyncio.wait_for", return_value=(b"Error", b"")):
            mock_exec.return_value = mock_proc
            with patch.object(AnsibleExecutor, "_parse_adhoc_line_by_line") as mock_parse:
                mock_parse.return_value = [AnsibleTaskResult(host="host1", state=ChangeState.FAILED, return_code=1)]
                await executor._verify_state(result, "false")

    assert task.verification_status == VerificationStatus.VERIFICATION_FAILED

@pytest.mark.asyncio
async def test_verify_state_skip_on_failure():
    # Scenario: Action FAIL -> Verification NOT_REQUESTED
    executor = AnsibleExecutor()
    task = AnsibleTaskResult(host="host1", state=ChangeState.FAILED, failed=True, unreachable=False)
    result = AnsibleOperationResult(
        operation_id="op3",
        command=["ansible", "host1", "-m", "shell", "-a", "echo 1"],
        task_results=[task]
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        await executor._verify_state(result, "true")
        mock_exec.assert_not_called()

    assert task.verification_status == VerificationStatus.NOT_REQUESTED

@pytest.mark.asyncio
async def test_verify_state_timeout():
    # Scenario: Action OK -> Verify Timeout -> INSUFFICIENT_DATA
    executor = AnsibleExecutor()
    task = AnsibleTaskResult(host="host1", state=ChangeState.CHANGED, failed=False, unreachable=False)
    result = AnsibleOperationResult(
        operation_id="op4",
        command=["ansible", "host1", "-m", "shell", "-a", "echo 1"],
        task_results=[task]
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_exec.return_value = mock_proc
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            await executor._verify_state(result, "true")

    assert task.verification_status == VerificationStatus.INSUFFICIENT_DATA

@pytest.mark.asyncio
async def test_verify_state_multi_host_isolation():
    # Scenario: Host1 OK/Verified, Host2 OK/Fail
    executor = AnsibleExecutor()
    t1 = AnsibleTaskResult(host="host1", state=ChangeState.CHANGED, failed=False, unreachable=False)
    t2 = AnsibleTaskResult(host="host2", state=ChangeState.CHANGED, failed=False, unreachable=False)
    result = AnsibleOperationResult(
        operation_id="op5",
        command=["ansible", "host1,host2", "-m", "shell", "-a", "echo 1"],
        task_results=[t1, t2]
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"...", b"")
        mock_proc.returncode = 0
        with patch("asyncio.wait_for", return_value=(b"...", b"")):
            mock_exec.return_value = mock_proc
            with patch.object(AnsibleExecutor, "_parse_adhoc_line_by_line") as mock_parse:
                mock_parse.return_value = [
                    AnsibleTaskResult(host="host1", state=ChangeState.UNCHANGED, return_code=0),
                    AnsibleTaskResult(host="host2", state=ChangeState.FAILED, return_code=1)
                ]
                await executor._verify_state(result, "true")

    assert t1.verification_status == VerificationStatus.VERIFIED
    assert t2.verification_status == VerificationStatus.VERIFICATION_FAILED
