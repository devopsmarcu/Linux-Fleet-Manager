from __future__ import annotations

import json
import os
import getpass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from core.config import get_settings
from core.models import OperationStatus

class AuditEntry(BaseModel):
    """Entrada de auditoria para uma operação do LFM."""
    execution_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.now)
    user: str = Field(default_factory=getpass.getuser)
    action: str
    command: str
    targets: str
    status: OperationStatus
    duration: float
    hosts_success: int
    hosts_failed: int
    details: Optional[str] = None

class AuditManager:
    """
    Gerencia a persistência de auditoria de operações.
    Utiliza JSONL para garantir escritas atômicas e eficientes.
    """
    def __init__(self) -> None:
        settings = get_settings()
        self.audit_file: Path = settings.logs_dir / "audit.jsonl"

    def record_operation(self, entry: AuditEntry) -> None:
        """Persiste uma entrada de auditoria no arquivo JSONL."""
        with self.audit_file.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def get_history(self, limit: int = 100) -> List[AuditEntry]:
        """Retorna as últimas N operações registradas."""
        if not self.audit_file.exists():
            return []

        entries: List[AuditEntry] = []
        with self.audit_file.open("r", encoding="utf-8") as f:
            # Lê as linhas e inverte para pegar as mais recentes primeiro
            lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(AuditEntry.model_validate_json(line))
                except Exception:
                    continue
                if len(entries) >= limit:
                    break
        return entries

# Singleton para uso global
audit_manager = AuditManager()
