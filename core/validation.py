from __future__ import annotations

import re
from typing import NamedTuple

class ValidationResult(NamedTuple):
    is_destructive: bool
    contains_secrets: bool
    reason: str | None = None

class CommandValidator:
    """
    Validador de comandos para execução remota.

    Identifica a presença de segredos para garantir a privacidade nas execuções
    via `lfm exec`. A detecção de comandos destrutivos agora é simplificada,
    delegando a confirmação de segurança para a camada de UX/CLI.
    """

    # Padrões para detecção de segredos (senhas, tokens, chaves)
    _SECRET_PATTERNS = [
        # Flags comuns de senha: -p, --password, --token, --key, --secret
        re.compile(r"(\s-p\s|\s--password\s|\s--token\s|\s--key\s|\s--secret\s)", re.IGNORECASE),
        # Atribuições de variáveis: TOKEN=, PASSWORD=, API_KEY=, etc.
        re.compile(r"(\b(TOKEN|PASSWORD|PASS|API_KEY|SECRET|AUTH|KEY)\b\s*=\s*)", re.IGNORECASE),
        # Strings que parecem chaves privadas ou tokens longos (simplificado)
        re.compile(r"(\b[a-zA-Z0-9]{32,}\b)", re.IGNORECASE),
    ]

    @classmethod
    def validate(cls, command: str) -> ValidationResult:
        """
        Analisa o comando em busca de segredos.

        Nota: a detecção de 'destructive' foi removida pois blacklists de regex
        são insuficientes para segurança real. A camada de CLI deve solicitar
        confirmação para qualquer comando exec.
        """
        # By default, we treat all 'exec' commands as potentially destructive
        # to force a confirmation prompt in the CLI.
        is_destructive = True

        contains_secrets = False
        for pattern in cls._SECRET_PATTERNS:
            if pattern.search(command):
                contains_secrets = True
                break

        return ValidationResult(
            is_destructive=is_destructive,
            contains_secrets=contains_secrets,
            reason="Execução de comandos remotos requer confirmação explícita"
        )

    @classmethod
    def is_destructive(cls, command: str) -> bool:
        """Atalho para verificar se o comando é destrutivo."""
        return cls.validate(command).is_destructive

    @classmethod
    def contains_secrets(cls, command: str) -> bool:
        """Atalho para verificar se o comando contém segredos."""
        return cls.validate(command).contains_secrets
