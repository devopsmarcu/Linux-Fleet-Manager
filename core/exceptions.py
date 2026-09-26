from __future__ import annotations

from typing import Any


class LFMError(Exception):
    """Base exception for all LFM errors."""

    message: str
    details: dict[str, Any]

    def __init__(self, message: str, **details: Any) -> None:
        self.message = message
        self.details = details
        super().__init__(message)

    def __str__(self) -> str:
        if not self.details:
            return self.message
        details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
        return f"{self.message} ({details_str})"


class ConfigError(LFMError):
    """Raised when configuration is invalid or missing."""


class InventoryError(LFMError):
    """Raised when inventory loading or validation fails."""


class AnsibleError(LFMError):
    """Raised when Ansible execution fails."""


class AnsibleUnreachableError(AnsibleError):
    """Raised when a host is unreachable via SSH."""


class AnsibleAuthError(AnsibleError):
    """Raised when authentication to a host fails."""


class AnsibleTimeoutError(AnsibleError):
    """Raised when an Ansible operation times out."""


class CommandExecutionError(LFMError):
    """Raised when a CLI command execution fails."""


class ReportError(LFMError):
    """Raised when report generation fails."""
