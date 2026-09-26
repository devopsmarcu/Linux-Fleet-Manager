from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.exceptions import InventoryError, LFMError
from core.inventory import InventoryManager, SystemInfoCollector
from core.logger import get_logger
from core.models import HostStatusEnum, HostSystemInfo

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
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Mostrar somente o host solicitado.",
    ),
    group: str | None = typer.Option(
        None,
        "--group",
        "-g",
        help="Filtrar por grupo do inventário.",
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
    """[bold]lfm inventory[/bold] — descobre e exibe informações detalhadas dos hosts Linux."""
    if ctx.invoked_subcommand is None:
        if isinstance(host, typer.models.OptionInfo):
            host = None
        if isinstance(group, typer.models.OptionInfo):
            group = None
        if isinstance(as_json, typer.models.OptionInfo):
            as_json = False
        if isinstance(timeout, typer.models.OptionInfo):
            timeout = 10

        collect_and_display(host_name=host, group=group, as_json=as_json, timeout=timeout)


@app.command("collect")
def collect_cmd(
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Filtrar por host específico.",
    ),
    group: str | None = typer.Option(
        None,
        "--group",
        "-g",
        help="Filtrar por grupo específico.",
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
    """Executa a coleta detalhada via Ansible e exibe tabela ou JSON."""
    if isinstance(host, typer.models.OptionInfo):
        host = None
    if isinstance(group, typer.models.OptionInfo):
        group = None
    if isinstance(as_json, typer.models.OptionInfo):
        as_json = False
    if isinstance(timeout, typer.models.OptionInfo):
        timeout = 10

    collect_and_display(host_name=host, group=group, as_json=as_json, timeout=timeout)


def collect_and_display(
    host_name: str | None = None,
    group: str | None = None,
    as_json: bool = False,
    timeout: int = 10,
) -> None:
    manager = _manager()
    collector = SystemInfoCollector(inventory_manager=manager)
    items: list[HostSystemInfo] = collector.collect(
        group=group, host_name=host_name, timeout=timeout
    )

    if as_json:
        payload = [item.model_dump(mode="json") for item in items]
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    console = Console()
    console.print()
    console.print("[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]Inventário de Hosts[/dim]")
    console.print("─" * 60)

    if not items:
        console.print(
            Panel("Nenhum host encontrado.", title="Inventário Vazio", border_style="yellow")
        )
        return

    table = Table(show_lines=False)
    table.add_column("HOST", style="cyan", no_wrap=True)
    table.add_column("IP", style="green")
    table.add_column("OS", style="bold")
    table.add_column("VERSION")
    table.add_column("KERNEL", style="dim")
    table.add_column("CPU", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("DISK", justify="right")
    table.add_column("UPTIME", justify="right")

    for item in items:
        if item.status == HostStatusEnum.ONLINE:
            os_val = item.distribution or "Linux"
            ver_val = item.distribution_version or "—"
            kernel_val = item.kernel or "—"
            cpu_val = str(item.cpu_cores) if item.cpu_cores else "—"
            ram_val = item.ram_display
            disk_val = item.disk_display
            uptime_val = item.uptime_display
            ip_val = item.ip or item.address
        else:
            os_val = "[bold red]OFFLINE[/bold red]"
            ver_val = "[dim]—[/dim]"
            kernel_val = "[dim]—[/dim]"
            cpu_val = "[dim]—[/dim]"
            ram_val = "[dim]—[/dim]"
            disk_val = "[dim]—[/dim]"
            uptime_val = "[dim]—[/dim]"
            ip_val = item.address or "—"

        table.add_row(
            item.host,
            ip_val,
            os_val,
            ver_val,
            kernel_val,
            cpu_val,
            ram_val,
            disk_val,
            uptime_val,
        )

    console.print(table)
    console.print()
    total = len(items)
    online = sum(1 for i in items if i.status == HostStatusEnum.ONLINE)
    offline = total - online
    console.print(
        f"[bold]Total:[/bold] {total}  ·  "
        f"[bold green]Online:[/bold green] {online}  ·  "
        f"[bold red]Offline:[/bold red] {offline}"
    )


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
    """Lista rápida dos hosts cadastrados no inventário (sem rodar Ansible)."""
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

    console = Console()
    if not hosts:
        console.print(
            Panel("Nenhum host cadastrado.", title="Inventário Vazio", border_style="yellow")
        )
        return

    table = Table(title="Inventário LFM (Cadastrado)", show_lines=False)
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
