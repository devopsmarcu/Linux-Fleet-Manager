from __future__ import annotations

import time
import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ansible.runner import AnsibleExecutor
from core.config import get_settings
from core.exceptions import LFMError
from core.logger import get_logger
from core.validation import CommandValidator
from core.audit import audit_manager, AuditEntry
from core.models import OperationStatus

logger = get_logger("cli.commands.exec")
console = Console()

app = typer.Typer(help="Executar comandos shell arbitrários nos hosts.")

@app.command("run")
def run(
    command: str = typer.Argument(..., help="Comando shell a ser executado."),
    host: str | None = typer.Option(None, "--host", help="Executar em um host específico."),
    group: str | None = typer.Option(None, "--group", help="Executar em um grupo de hosts."),
    become: bool = typer.Option(True, help="Executar com privilégios de root (become)."),
    timeout: int | None = typer.Option(None, help="Tempo limite para a execução em segundos."),
) -> None:
    """
    Executa um comando shell em hosts remotos via Ansible.

    Exemplos:
      lfm exec run "uptime"
      lfm exec run --host ubuntu-01 "df -h"
      lfm exec run --group printers "systemctl status cups"
    """
    # 1. Validação de Segurança e Privacidade
    val_result = CommandValidator.validate(command)

    # Detecção de Segredos
    if val_result.contains_secrets:
        logger.warning(
            "Comando contém possíveis segredos/credenciais. "
            "O comando será omitido dos logs detalhados para segurança.",
            extra={"lfm_contains_secrets": True}
        )
        log_command = "[REDACTED]"
    else:
        log_command = command

    # Confirmação para Comandos Destrutivos
    if val_result.is_destructive:
        rprint(f"[bold red]⚠️  AVISO DE SEGURANÇA[/bold red]")
        rprint(f"[yellow]{val_result.reason}[/yellow]")
        rprint(f"Comando: [bold white]{command}[/bold white]")
        if not typer.confirm("Você tem certeza que deseja executar este comando?"):
            rprint("[bold red]Operação cancelada pelo usuário.[/bold red]")
            raise typer.Exit()

    # 2. Definição de Alvos (Targets)
    targets = "all"
    if host:
        targets = host
    elif group:
        targets = group

    # 3. Execução via Ansible
    try:
        start_time = time.time()
        executor = AnsibleExecutor(timeout=timeout)

        logger.info(
            "Executando comando remoto",
            extra={
                "lfm_command": log_command,
                "lfm_targets": targets,
                "lfm_become": become,
            }
        )

        # Usamos o módulo 'shell' para permitir pipes e redirecionamentos
        result = executor.adhoc(
            module="shell",
            args=command,
            targets=targets,
            become=become,
            timeout=timeout,
        )

        # Auditoria da operação
        duration = time.time() - start_time

        # Mapeamento de status
        status = OperationStatus.SUCCESS
        if not result.success:
            if len(result.hosts_failed) > 0:
                status = OperationStatus.FAILED
            elif len(result.hosts_unreachable) > 0 or result.hosts_total == 0:
                status = OperationStatus.WARNING
            else:
                status = OperationStatus.FAILED

        audit_manager.record_operation(
            AuditEntry(
                action="exec",
                command=log_command,
                targets=targets,
                status=status,
                duration=duration,
                hosts_success=len(result.hosts_ok),
                hosts_failed=len(result.hosts_failed) + len(result.hosts_unreachable),
                details=f"Operation ID: {result.operation_id}"
            )
        )

        # 4. Formatação e Exibição dos Resultados
        if not result.task_results:
            rprint("[yellow]Nenhum resultado retornado dos hosts.[/yellow]")
            return

        for task in result.task_results:
            # Cor do status baseada no resultado
            status_color = "green" if not task.failed and not task.unreachable else "red"
            if task.unreachable:
                status_text = "UNREACHABLE"
            elif task.failed:
                status_text = f"FAILED (rc={task.return_code})"
            else:
                status_text = f"SUCCESS (rc={task.return_code})"

            # Painel para cada host
            host_header = Text(f"{task.host}", style="bold cyan")
            host_header.append(f" - {status_text}", style=status_color)

            content = []
            if task.stdout:
                content.append(f"[bold green]stdout:[/bold green]\n{task.stdout}")
            if task.stderr:
                content.append(f"[bold red]stderr:[/bold red]\n{task.stderr}")
            if task.msg:
                content.append(f"[bold yellow]msg:[/bold yellow]\n{task.msg}")

            if not content:
                content.append("[dim]Sem saída disponível.[/dim]")

            console.print(
                Panel(
                    "\n".join(content),
                    title=host_header,
                    border_style=status_color,
                    expand=False
                )
            )

    except LFMError as e:
        rprint(f"[bold red]Erro na execução:[/bold red] {e.message}")
        if e.details:
            for k, v in e.details.items():
                rprint(f"  [dim]{k}[/dim]: {v}")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.exception("Erro inesperado durante a execução remota")
        rprint(f"[bold red]Erro inesperado:[/bold red] {str(e)}")
        raise typer.Exit(code=1)
