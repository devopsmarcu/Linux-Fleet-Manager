"""Módulo central de integração com Ansible do Linux Fleet Manager."""

from __future__ import annotations

from ansible.runner import AnsibleExecutor

# Alias AnsibleRunner para compatibilidade
AnsibleRunner = AnsibleExecutor

__all__ = [
    "AnsibleExecutor",
    "AnsibleRunner",
]
