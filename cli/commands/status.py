from __future__ import annotations

import time
from typing import Optional

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from core.logger import get_logger

logger = get_logger("cli.commands.status")

app = typer.Typer(
    name="status",
    help="Verificar status de conectividade dos hosts.",
    no_args_is_help=False,
)

_MOCK_HOSTS = [
    {"name": "web-01", "address": "192.168.1.10", "simulated": "ok"},
    {"name": "web-02", "address": "192.168.1.11", "simulated": "ok"},
    {"name": "db-01", "address": "192.168.1.20", "simulated": "slow"},
    {"name": "ws-01", "address": "192.168.1.30", "simulated": "unreachable"},
]


@app.command()
def check(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        "-H",
        help="Verificar status de um host específico.",
    ),
    group: Optional[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Filtrar hosts por grupo (simulado no MVP).",
    ),
) -> None:
    """Verifica conectividade SSH/ping dos hosts do inventário."""
    logger.info(
        "Iniciando checagem de status",
        extra={"lfm_filter_host": host or "all", "lfm_filter_group": group or "all"},
    )

    targets = _MOCK_HOSTS
    if host:
        targets = [h for h in targets if h["name"] == host]

    console = Console()
    results = []

    with Status("[bold cyan]Verificando status dos hosts...[/bold cyan]", console=console, spinner="dots"):
        for h in targets:
            t0 = time.perf_counter()
            time.sleep(0.15)
            if h["simulated"] == "ok":
                state, latency = "OK", 12 + (hash(h["name"]) % 20)
            elif h["simulated"] == "slow":
                state, latency = "OK", 450 + (hash(h["name"]) % 150)
            else:
                state, latency = "INALCANÇÁVEL", 0
            duration = int((time.perf_counter() - t0) * 1000)
            results.append({
                "name": h["name"],
                "address": h["address"],
                "status": state,
                "latency_ms": latency,
                "duration_ms": duration,
            })
            logger.debug(
                "Status verificado",
                extra={
                    "lfm_host": h["name"],
                    "lfm_status": state,
                    "lfm_latency_ms": latency,
                },
            )

    table = Table(title="Status dos Hosts")
    table.add_column("Host", style="cyan")
    table.add_column("Endereço", style="green")
    table.add_column("Status", style="bold")
    table.add_column("Latência", justify="right")

    ok_count = 0
    for r in results:
        if r["status"] == "OK":
            status_style = "bold green"
            ok_count += 1
            latency_str = f"{r['latency_ms']} ms"
        else:
            status_style = "bold red"
            latency_str = "[dim]—[/dim]"
        table.add_row(
            r["name"],
            r["address"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            latency_str,
        )

    console.print(table)

    total = len(results)
    fail = total - ok_count
    console.print()
    console.print(
        f"[bold]Resumo:[/bold] [green]{ok_count} OK[/green] / "
        f"[red]{fail} inalcançável(is)[/red] de {total} host(s)"
    )
    logger.info(
        "Checagem de status finalizada",
        extra={"lfm_total": total, "lfm_ok": ok_count, "lfm_fail": fail},
    )
