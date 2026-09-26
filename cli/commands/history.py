from __future__ import annotations

import typer
from rich import print as rprint
from rich.table import Table
from rich.console import Console

from core.audit import audit_manager
from core.models import OperationStatus

app = typer.Typer(help="Exibir histórico de operações do LFM.")
console = Console()

@app.command("list")
def list_history(
    limit: int = typer.Option(100, help="Número máximo de entradas para exibir."),
) -> None:
    """Exibe a lista de operações anteriores registradas no sistema."""
    entries = audit_manager.get_history(limit=limit)

    if not entries:
        rprint("[yellow]Nenhum histórico de operações encontrado.[/yellow]")
        return

    table = Table(
        show_header=True,
        header_style="bold magenta",
        box=None,
        padding=(0, 2)
    )
    table.add_column("TIMESTAMP", style="dim", width=20)
    table.add_column("ACTION", style="cyan", width=15)
    table.add_column("TARGET", style="green")
    table.add_column("STATUS", justify="right")

    for entry in entries:
        # Formatação do target (encurta se for muito longo)
        target_str = entry.targets
        if len(target_str) > 30:
            target_str = target_str[:27] + "..."

        # Cor do status
        status_color = {
            OperationStatus.SUCCESS: "green",
            OperationStatus.FAILED: "red",
            OperationStatus.WARNING: "yellow",
        }.get(entry.status, "white")

        table.add_row(
            entry.timestamp.strftime("%Y-%m-%d %H:%M"),
            entry.action,
            target_str,
            f"[{status_color}]{entry.status}[/{status_color}]"
        )

    console.print(table)
