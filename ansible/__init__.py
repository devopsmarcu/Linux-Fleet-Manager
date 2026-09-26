"""Módulo de integração com Ansible.

Responsabilidades futuras:
- Wrapper fino sobre binários `ansible` e `ansible-playbook` via subprocess
- Parseamento de stdout JSON do callback JSON do Ansible
- Mapeamento de códigos de saída/erros para exceções LFM (AnsibleError, etc.)
- Execução ad-hoc e playbooks com targets parametrizados

Este módulo intencionalmente vazio no MVP — interface será exposta em versões seguintes.
"""

from __future__ import annotations

from typing import Any


class AnsibleRunner:
    """Interface preparada para integração futura com Ansible.

    Nenhum método é implementado ainda. Stub existe para:
    - Servir de contrato para a camada de serviços (services/*)
    - Permitir injeção de mock nos testes
    - Documentar a API planejada
    """

    def __init__(self, **kwargs: Any) -> None:
        self.config = kwargs

    def run_adhoc(
        self,
        module: str,
        args: str | None = None,
        targets: str = "all",
        **extra: Any,
    ) -> list[dict[str, Any]]:
        """Executa módulo ad-hoc do Ansible. Não implementado no MVP."""
        raise NotImplementedError("Integração Ansible será disponibilizada em versão futura.")

    def run_playbook(
        self,
        playbook: str,
        targets: str = "all",
        extra_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Executa um playbook Ansible. Não implementado no MVP."""
        raise NotImplementedError("Integração Ansible será disponibilizada em versão futura.")
