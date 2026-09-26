from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from core.config import get_settings
from core.logger import get_logger

logger = get_logger("cli.commands.inventory")

app = typer.Typer(
    name="inventory",
    help="Gerenciar e visualizar inventário de hosts.",
    no_args_is_help=True,
)

_MOCK_HOSTS = [
    {"name": "web-01", "address": "192.168.1.10", "groups": "ubuntu,web", "os": "Ubuntu 24.04"},
    {"name": "web-02", "address": "192.168.1.11", "groups": "ubuntu,web", "os": "Ubuntu 24.04"},
    {"name": "db-01", "address": "192.168.1.20", "groups": "debian,db", "os": "Debian 12"},
    {"name": "ws-01", "address": "192.168.1.30", "groups": "xubuntu,workstation", "os": "Xubuntu 24.04"},
]


@app.command("list")
def list_hosts(
    group: Optional[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="Filtrar hosts por grupo.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Mostrar detalhes adicionais.",
    ),
) -> None:
    """Lista todos os hosts cadastrados no inventário."""
    settings = get_settings()
    logger.info(
        "Listando inventário",
        extra={"lfm_filter_group": group or "all", "lfm_inventory_dir": str(settings.inventory_dir)},
    )

    hosts = _MOCK_HOSTS
    if group:
        hosts = [h for h in hosts if group in h["groups"].split(",")]

    console = Console()
    table = Table(title=f"Inventário LFM ({len(hosts)} host(s))", show_lines=False)
    table.add_column("Host", style="cyan", no_wrap=True)
    table.add_column("Endereço", style="green")
    table.add_column("Grupos", style="yellow")
    if verbose:
        table.add_column("Sistema", style="magenta")

    for h in hosts:
        row = [h["name"], h["address"], h["groups"]]
        if verbose:
            row.append(h["os"])
        table.add_row(*row)

    console.print(table)

    if not hosts and group:
        logger.warning("Nenhum host encontrado para o grupo informado", extra={"lfm_group": group})


@app.command("groups")
def list_groups() -> None:
    """Lista todos os grupos definidos no inventário."""
    logger.info("Listando grupos do inventário")
    groups: dict[str, int] = {}
    for h in _MOCK_HOSTS:
        for g in h["groups"].split(","):
            g = g.strip()
            groups[g] = groups.get(g, 0) + 1

    console = Console()
    table = Table(title="Grupos do Inventário", show_header=True)
    table.add_column("Grupo", style="cyan")
    table.add_column("# Hosts", justify="right", style="green")
    for g, count in sorted(groups.items()):
        table.add_row(g, str(count))
    console.print(table)
