from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.health import HealthChecker
from core.logger import get_logger
from core.models import HealthState, HostHealthReport

logger = get_logger("cli.commands.health")

app = typer.Typer(
    name="health",
    help="Avaliar a saúde das máquinas Linux.",
    no_args_is_help=False,
)

_STATUS_STYLE = {
    HealthState.HEALTHY: "bold green",
    HealthState.WARNING: "bold yellow",
    HealthState.CRITICAL: "bold red",
}


@app.callback(invoke_without_command=True)
def health_root(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Executar health check em host específico.",
    ),
    group: str | None = typer.Option(
        None,
        "--group",
        "-g",
        help="Executar health check em grupo específico.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Retornar os dados em JSON válido.",
    ),
    timeout: int = typer.Option(
        10,
        "--timeout",
        "-t",
        help="Timeout em segundos por host SSH.",
        min=1,
        max=300,
    ),
) -> None:
    """[bold]lfm health[/bold] — avalia a saúde básica das máquinas Linux (conectividade, SSH, disco, memória, CPU, serviços, atualizações, DNS, rede)."""
    if ctx.invoked_subcommand is None:
        if isinstance(host, typer.models.OptionInfo):
            host = None
        if isinstance(group, typer.models.OptionInfo):
            group = None
        if isinstance(as_json, typer.models.OptionInfo):
            as_json = False
        if isinstance(timeout, typer.models.OptionInfo):
            timeout = 10

        run_health_check(host_name=host, group=group, as_json=as_json, timeout=timeout)


@app.command("run")
def run_cmd(
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Executar em host específico.",
    ),
    group: str | None = typer.Option(
        None,
        "--group",
        "-g",
        help="Executar em grupo específico.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Exibir em formato JSON.",
    ),
    timeout: int = typer.Option(
        10,
        "--timeout",
        "-t",
        help="Timeout em segundos.",
    ),
) -> None:
    """Executa o checklist de saúde nos hosts."""
    if isinstance(host, typer.models.OptionInfo):
        host = None
    if isinstance(group, typer.models.OptionInfo):
        group = None
    if isinstance(as_json, typer.models.OptionInfo):
        as_json = False
    if isinstance(timeout, typer.models.OptionInfo):
        timeout = 10

    run_health_check(host_name=host, group=group, as_json=as_json, timeout=timeout)


def run_health_check(
    host_name: str | None = None,
    group: str | None = None,
    as_json: bool = False,
    timeout: int = 10,
) -> None:
    checker = HealthChecker()
    reports: list[HostHealthReport] = checker.run_health_check(
        group=group,
        host_name=host_name,
        timeout=timeout,
    )

    if as_json:
        payload = [rep.model_dump(mode="json") for rep in reports]
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        _exit_by_reports(reports)
        return

    console = Console()
    console.print()
    console.print("[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]Health Check[/dim]")
    console.print("─" * 50)

    if not reports:
        console.print(Panel("Nenhum host encontrado.", title="Sem Resultados", border_style="yellow"))
        raise typer.Exit(code=0)

    table = Table(show_lines=False)
    table.add_column("HOST", style="cyan", no_wrap=True)
    table.add_column("STATUS", style="bold", justify="center")
    table.add_column("ISSUES", style="white")

    healthy_count = 0
    warning_count = 0
    critical_count = 0

    for rep in reports:
        style = _STATUS_STYLE.get(rep.status, "bold")
        status_cell = f"[{style}]{rep.status.value}[/{style}]"
        table.add_row(rep.host, status_cell, rep.issues_display)

        if rep.status == HealthState.HEALTHY:
            healthy_count += 1
        elif rep.status == HealthState.WARNING:
            warning_count += 1
        elif rep.status == HealthState.CRITICAL:
            critical_count += 1

    console.print(table)
    console.print()
    total = len(reports)
    console.print(
        f"[bold]Total:[/bold] {total}  ·  "
        f"[bold green]Healthy:[/bold green] {healthy_count}  ·  "
        f"[bold yellow]Warning:[/bold yellow] {warning_count}  ·  "
        f"[bold red]Critical:[/bold red] {critical_count}"
    )

    _exit_by_reports(reports)


def _exit_by_reports(reports: list[HostHealthReport]) -> None:
    if any(r.status == HealthState.CRITICAL for r in reports):
        raise typer.Exit(code=2)
    if any(r.status == HealthState.WARNING for r in reports):
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)
