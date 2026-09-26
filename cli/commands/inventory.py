from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.exceptions import InventoryError, LFMError
from core.inventory import InventoryManager
from core.logger import get_logger

logger = get_logger("cli.commands.inventory")

app = typer.Typer(
    name="inventory",
    help="Gerenciar e visualizar inventário de hosts.",
    no_args_is_help=False,
)


def _manager() -> InventoryManager:
    try:
        return InventoryManager()
    except InventoryError as exc:
        logger.error(str(exc), extra={"lfm_error_details": exc.details})
        console = Console()
        console.print(
            Panel(
                f"[bold red]{exc.message}[/bold red]\n"
                f"[dim]Verifique o caminho e crie o arquivo a partir do template hosts.ini[/dim]",
                title="Erro de Inventário",
                border_style="red",
            )
        )
        raise typer.Exit(code=2) from exc


@app.callback(invoke_without_command=True)
def inventory_root(
    ctx: typer.Context,
    list_all: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="Lista todos os hosts (equivalente a lfm inventory list).",
    ),
) -> None:
    """[bold]lfm inventory[/bold] — gerencia e visualiza o inventário Ansible."""
    if ctx.invoked_subcommand is None or list_all:
        list_hosts(group=None, host_name=None, verbose=False)


@app.command("list")
def list_hosts(
    group: str | None = typer.Option(
        None,
        "--group",
        "-g",
        help="Filtrar hosts por grupo.",
    ),
    host_name: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Filtrar por nome de host específico.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Mostrar detalhes adicionais (usuário SSH, porta, conexão).",
    ),
) -> None:
    """Lista todos os hosts cadastrados no inventário."""
    if isinstance(group, typer.models.OptionInfo):
        group = None
    if isinstance(host_name, typer.models.OptionInfo):
        host_name = None
    if isinstance(verbose, typer.models.OptionInfo):
        verbose = False

    manager = _manager()
    try:
        hosts = manager.list_hosts(group=group, host_name=host_name)
    except LFMError as exc:
        logger.error(str(exc), extra={"lfm_error_details": exc.details})
        raise typer.Exit(code=2) from exc

    logger.info(
        "Inventário listado",
        extra={
            "lfm_filter_group": group or "all",
            "lfm_filter_host": host_name or "all",
            "lfm_hosts_count": len(hosts),
        },
    )

    console = Console()
    if not hosts:
        msg = "Nenhum host encontrado no inventário."
        if group:
            msg += f" Filtro: grupo=[yellow]{group}[/yellow]"
        if host_name:
            msg += f" Filtro: host=[yellow]{host_name}[/yellow]"
        msg += (
            "\n[dim]Descomente as linhas de exemplo em [bold]ansible/inventory/hosts.ini[/bold]"
            " ou adicione seus próprios hosts.[/dim]"
        )
        console.print(Panel(msg, title="Inventário Vazio", border_style="yellow"))
        return

    title = "Inventário LFM"
    if group:
        title += f" · grupo: {group}"
    if host_name:
        title += f" · host: {host_name}"

    table = Table(title=f"{title} ({len(hosts)} host(s))", show_lines=False)
    table.add_column("Host", style="cyan", no_wrap=True)
    table.add_column("Endereço", style="green")
    table.add_column("Porta", justify="right")
    table.add_column("Grupos", style="yellow")
    if verbose:
        table.add_column("Usuário", style="magenta")
        table.add_column("Conexão", style="dim")

    for h in hosts:
        grupos = ",".join(h.groups) if h.groups else "[dim]—[/dim]"
        row = [h.name, h.address, str(h.port), grupos]
        if verbose:
            row.append(h.user or "[dim]—[/dim]")
            row.append(f"[dim]{h.connection_label}[/dim]")
        table.add_row(*row)

    console.print(table)

    summary = manager.summary()
    console.print(
        f"[dim]Total grupos cadastrados: [bold]{summary.total_groups}[/bold][/dim]"
    )


@app.command("groups")
def list_groups() -> None:
    """Lista todos os grupos definidos no inventário com contagem de hosts."""
    manager = _manager()
    logger.info("Listando grupos do inventário")
    groups = manager.list_groups()

    console = Console()
    table = Table(title="Grupos do Inventário", show_header=True)
    table.add_column("Grupo", style="cyan")
    table.add_column("# Hosts", justify="right", style="green")
    for g, count in groups.items():
        table.add_row(g, str(count))
    console.print(table)
