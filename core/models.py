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


class HostSystemInfo(BaseModel):
    """Informações completas do sistema coletadas via Ansible de um host."""

    host: str = Field(..., description="Nome do host no inventário")
    address: str = Field(default="", description="Endereço IP ou hostname do inventário")
    status: HostStatusEnum = HostStatusEnum.UNKNOWN
    hostname: str | None = None
    ip: str | None = None
    distribution: str | None = None
    distribution_version: str | None = None
    kernel: str | None = None
    architecture: str | None = None
    cpu_model: str | None = None
    cpu_cores: int | None = None
    memory_total_mb: int | None = None
    disk_total_gb: float | None = None
    disk_used_gb: float | None = None
    uptime_seconds: int | None = None
    remote_user: str | None = None
    python_version: str | None = None
    services: list[str] = Field(default_factory=list)
    pending_updates: int | None = 0
    error_message: str | None = None
    checked_at: datetime = Field(default_factory=datetime.now)

    @property
    def os_display(self) -> str:
        if self.status != HostStatusEnum.ONLINE or not self.distribution:
            return "N/A"
        ver = f" {self.distribution_version}" if self.distribution_version else ""
        return f"{self.distribution}{ver}"

    @property
    def cpu_display(self) -> str:
        if self.status != HostStatusEnum.ONLINE:
            return "N/A"
        cores = f"{self.cpu_cores} cores" if self.cpu_cores else ""
        if self.cpu_model:
            return f"{self.cpu_model} ({cores})" if cores else self.cpu_model
        return cores or "N/A"

    @property
    def ram_display(self) -> str:
        if self.status != HostStatusEnum.ONLINE or not self.memory_total_mb:
            return "N/A"
        if self.memory_total_mb >= 1024:
            return f"{self.memory_total_mb / 1024:.1f} GB"
        return f"{self.memory_total_mb} MB"

    @property
    def disk_display(self) -> str:
        if self.status != HostStatusEnum.ONLINE:
            return "N/A"
        if self.disk_used_gb is not None and self.disk_total_gb is not None:
            return f"{self.disk_used_gb:.1f} / {self.disk_total_gb:.1f} GB"
        if self.disk_total_gb is not None and self.disk_total_gb > 0:
            return f"{self.disk_total_gb:.1f} GB"
        return "N/A"

    @property
    def uptime_display(self) -> str:
        if self.status != HostStatusEnum.ONLINE or not self.uptime_seconds:
            return "N/A"
        total = int(self.uptime_seconds)
        days, remainder = divmod(total, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0 or days > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)

