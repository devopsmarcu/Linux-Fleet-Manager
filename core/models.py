from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HostStatusEnum(enum.StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"
    AUTH_ERROR = "AUTH_ERROR"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class ChangeState(enum.StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNREACHABLE = "unreachable"


class Host(BaseModel):
    """Representa um host do inventário."""

    name: str = Field(..., description="Nome lógico do host (inventory_hostname)")
    address: str = Field(..., description="Endereço IP ou FQDN usado para conectar")
    groups: list[str] = Field(default_factory=list)
    vars: dict[str, Any] = Field(default_factory=dict)
    user: str | None = None
    port: int = 22

    @property
    def connection_label(self) -> str:
        user = f"{self.user}@" if self.user else ""
        port = f":{self.port}" if self.port != 22 else ""
        return f"{user}{self.address}{port}"


class HostStatus(BaseModel):
    """Resultado da checagem de status / ping de um único host."""

    host: Host
    status: HostStatusEnum = HostStatusEnum.UNKNOWN
    latency_ms: int | None = None
    changed: bool = False
    error_message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
    checked_at: datetime = Field(default_factory=datetime.now)


class AnsibleTaskResult(BaseModel):
    """Resultado de uma task ou módulo ad-hoc em 1 host."""

    host: str
    task: str = "adhoc"
    state: ChangeState
    changed: bool = False
    failed: bool = False
    unreachable: bool = False
    skipped: bool = False
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    msg: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0


class AnsibleOperationResult(BaseModel):
    """Resultado agregado de uma operação Ansible (adhoc ou playbook)."""

    operation_id: str
    command: list[str]
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    return_code: int = 0
    success: bool = True
    raw_stdout: str = ""
    raw_stderr: str = ""
    task_results: list[AnsibleTaskResult] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def hosts_total(self) -> int:
        return len({r.host for r in self.task_results})

    @property
    def hosts_unreachable(self) -> list[str]:
        return sorted({r.host for r in self.task_results if r.unreachable})

    @property
    def hosts_failed(self) -> list[str]:
        return sorted({r.host for r in self.task_results if r.failed and not r.unreachable})

    @property
    def hosts_ok(self) -> list[str]:
        failed = self.hosts_unreachable + self.hosts_failed
        return sorted({r.host for r in self.task_results if r.host not in failed})

    def finalize(self) -> None:
        if self.finished_at is None:
            self.finished_at = datetime.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        self.success = (
            self.return_code in (0,)
            and not self.hosts_failed
            and len(self.hosts_unreachable) < self.hosts_total
        ) or (self.hosts_total == 0 and self.return_code == 0)


class InventorySummary(BaseModel):
    total_hosts: int = 0
    total_groups: int = 0
    groups: dict[str, int] = Field(default_factory=dict)
    hosts: list[Host] = Field(default_factory=list)
