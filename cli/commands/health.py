from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from core.logger import get_logger

logger = get_logger("cli.commands.health")

app = typer.Typer(
    name="health",
    help="Executar health checks nos hosts.",
    no_args_is_help=False,
)

_CHECKS = ["disk", "memory", "load", "reboot"]

_MOCK_RESULTS = {
    "web-01": {"disk": "OK", "memory": "OK", "load": "OK", "reboot": "OK"},
    "web-02": {"disk": "WARN", "memory": "OK", "load": "OK", "reboot": "OK"},
    "db-01": {"disk": "OK", "memory": "WARN", "load": "FAIL", "reboot": "WARN"},
    "ws-01": {"disk": "OK", "memory": "OK", "load": "OK", "reboot": "OK"},
}


@app.command()
def run(
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Executar health check em host específico.",
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        "-q",
        help="Modo rápido (menos checks).",
    ),
) -> None:
    """Executa checklist de saúde nos hosts (disco, memória, carga, reboot pendente)."""
    logger.info(
        "Iniciando health check",
        extra={"lfm_filter_host": host or "all", "lfm_quick": quick},
    )

    checks = _CHECKS[:2] if quick else _CHECKS
    hosts = list(_MOCK_RESULTS.keys())
    if host:
        hosts = [h for h in hosts if h == host]

    console = Console()

    status_msg = "[bold cyan]Executando health checks...[/bold cyan]"
    with Status(status_msg, console=console, spinner="dots"):
        time.sleep(0.6)

    table = Table(title=f"Health Check ({'rápido' if quick else 'completo'})")
    table.add_column("Host", style="cyan")
    for c in checks:
        table.add_column(c.upper(), justify="center")
    table.add_column("Geral", justify="center", style="bold")

    summary = {"OK": 0, "WARN": 0, "FAIL": 0}

    for h in hosts:
        row = [h]
        overall = "OK"
        host_results = _MOCK_RESULTS.get(h, {c: "OK" for c in checks})
        for c in checks:
            val = host_results.get(c, "OK")
            if val == "OK":
                style = "bold green"
            elif val == "WARN":
                style = "bold yellow"
                if overall == "OK":
                    overall = "WARN"
            else:
                style = "bold red"
                overall = "FAIL"
            row.append(f"[{style}]{val}[/{style}]")

        overall_style = {"OK": "bold green", "WARN": "bold yellow", "FAIL": "bold red"}[overall]
        row.append(f"[{overall_style}]{overall}[/{overall_style}]")
        summary[overall] += 1
        table.add_row(*row)
        logger.debug(
            "Health check concluído",
            extra={"lfm_host": h, "lfm_overall": overall},
        )

    console.print(table)
    console.print()
    console.print(
        f"[bold]Resumo:[/bold] "
        f"[green]{summary['OK']} saudável(is)[/green] · "
        f"[yellow]{summary['WARN']} aviso(s)[/yellow] · "
        f"[red]{summary['FAIL']} falha(s)[/red]"
    )
    logger.info(
        "Health check finalizado",
        extra={"lfm_summary": summary, "lfm_checks": checks},
    )
