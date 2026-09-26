from __future__ import annotations

import typer
from rich import print as rprint
from pathlib import Path

from core.reporting import report_generator

app = typer.Typer(help="Gerar relatórios consolidados da frota.")

@app.command("generate")
def generate(
    format: str = typer.Option(
        "html",
        "--format",
        help="Formato do relatório: json, csv, html."
    ),
) -> None:
    """Gera um relatório de auditoria consolidado e salva em reports/."""
    data = report_generator.aggregate_data()

    if not data:
        rprint("[yellow]Não há dados suficientes para gerar o relatório.[/yellow]")
        return

    timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"report_{timestamp}.{format.lower()}"

    try:
        if format.lower() == "json":
            path = report_generator.export_json(data, filename)
        elif format.lower() == "csv":
            path = report_generator.export_csv(data, filename)
        elif format.lower() == "html":
            path = report_generator.export_html(data, filename)
        else:
            rprint(f"[bold red]Erro:[/bold red] Formato '{format}' não suportado. Use json, csv ou html.")
            raise typer.Exit(code=1)

        rprint(f"[bold green]Relatório gerado com sucesso![/bold green]")
        rprint(f"Caminho: [cyan]{path}[/cyan]")

    except Exception as e:
        rprint(f"[bold red]Erro ao gerar relatório:[/bold red] {str(e)}")
        raise typer.Exit(code=1)
