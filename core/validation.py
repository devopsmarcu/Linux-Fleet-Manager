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

    Identifica comandos potencialmente destrutivos e a presença de segredos
    para garantir a segurança e a privacidade nas execuções via `lfm exec`.
    """

    # Padrões para comandos destrutivos
    # Não é uma simples blacklist, mas busca combinações de binários perigosos e flags/alvos
    _DESTRUCTIVE_PATTERNS = [
        # rm -rf / ou rm -rf * em diretórios raiz/sistema
        re.compile(r"rm\s+.*-rf\s+([/\s*]|$)", re.IGNORECASE),
        # Formatação de discos ou partições
        re.compile(r"(mkfs|fdisk|parted|dd\s+if=)\s+", re.IGNORECASE),
        # Comandos de desligamento ou reinicialização
        re.compile(r"(shutdown|reboot|poweroff|halt)\s*", re.IGNORECASE),
        # Redirecionamento para dispositivos de bloco (destrói o disco)
        re.compile(r">\s*/dev/sd[a-z][0-9]*", re.IGNORECASE),
        # Deleção recursiva de diretórios críticos
        re.compile(r"rm\s+.*-r\s+/(etc|var|bin|sbin|usr|root)", re.IGNORECASE),
    ]

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
        Analisa o comando em busca de comportamentos perigosos ou segredos.
        """
        is_destructive = False
        destructive_reason = None
        for pattern in cls._DESTRUCTIVE_PATTERNS:
            if pattern.search(command):
                is_destructive = True
                destructive_reason = "Comando identificado como potencialmente destrutivo"
                break

        contains_secrets = False
        for pattern in cls._SECRET_PATTERNS:
            if pattern.search(command):
                contains_secrets = True
                break

        return ValidationResult(
            is_destructive=is_destructive,
            contains_secrets=contains_secrets,
            reason=destructive_reason
        )

    @classmethod
    def is_destructive(cls, command: str) -> bool:
        """Atalho para verificar se o comando é destrutivo."""
        return cls.validate(command).is_destructive

    @classmethod
    def contains_secrets(cls, command: str) -> bool:
        """Atalho para verificar se o comando contém segredos."""
        return cls.validate(command).contains_secrets
