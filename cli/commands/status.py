from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table

from ansible.runner import AnsibleExecutor
from core.exceptions import (
    AnsibleAuthError,
    AnsibleError,
    AnsibleTimeoutError,
    AnsibleUnreachableError,
)
from core.logger import get_logger
from core.models import HostStatus, HostStatusEnum

logger = get_logger("cli.commands.status")

app = typer.Typer(
    name="status",
    help="Verificar status de conectividade dos hosts.",
    no_args_is_help=False,
)

_STATUS_STYLE = {
    HostStatusEnum.ONLINE: "bold green",
    HostStatusEnum.OFFLINE: "bold red",
    HostStatusEnum.AUTH_ERROR: "bold magenta",
    HostStatusEnum.TIMEOUT: "bold yellow",
    HostStatusEnum.ERROR: "bold red",
    HostStatusEnum.UNKNOWN: "bold dim",
}


@app.callback(invoke_without_command=True)
def status_root(
    ctx: typer.Context,
    targets: str = typer.Option(
        "all",
        "--targets",
        "-T",
        help="Alvo Ansible (grupo, host, pattern).",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Filtrar por nome de host específico.",
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
    """[bold]lfm status[/bold] — executa ping Ansible e reporta conectividade."""
    if ctx.invoked_subcommand is None:
        if isinstance(targets, typer.models.OptionInfo):
            targets = "all"
        if isinstance(host, typer.models.OptionInfo):
            host = None
        if isinstance(timeout, typer.models.OptionInfo):
            timeout = 10
        check(targets=targets, host=host, timeout=timeout)


@app.command("check")
def check(
    targets: str = typer.Argument(
        "all",
        help="Alvo Ansible (grupo, host, pattern). Ex: `ubuntu`, `db-01`, `all`.",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        "-H",
        help="Filtrar por nome de host específico.",
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
    """Executa `ansible <targets> -m ping` e reporta ONLINE / OFFLINE."""
    if isinstance(targets, (typer.models.ArgumentInfo, typer.models.OptionInfo)):
        targets = "all"
    if isinstance(host, (typer.models.ArgumentInfo, typer.models.OptionInfo)):
        host = None
    if isinstance(timeout, (typer.models.ArgumentInfo, typer.models.OptionInfo)):
        timeout = 10

    if host:
        targets = host
    logger.info(
        "Iniciando checagem de status (ping Ansible)",
        extra={"lfm_targets": targets, "lfm_timeout": timeout},
    )

    console = Console()
    executor: AnsibleExecutor | None = None
    results: list[HostStatus] = []

    try:
        executor = AnsibleExecutor(timeout=timeout)
    except AnsibleError as exc:
        logger.error(str(exc), extra={"lfm_error_details": exc.details})
        console.print(
            Panel(
                f"[bold red]{exc.message}[/bold red]\n[dim]{exc.details}[/dim]",
                title="Erro no Ansible",
                border_style="red",
            )
        )
        raise typer.Exit(code=2) from exc

    with Status(
        f"[bold cyan]Executando ping Ansible em [white]{targets}[/white]...[/bold cyan]",
        console=console,
        spinner="dots",
    ):
        t0 = time.perf_counter()
        try:
            results = executor.ping(targets=targets, timeout=timeout)
        except (AnsibleAuthError, AnsibleUnreachableError) as exc:
            # Algum host falhou durante parsing, já registramos individualmente
            logger.warning(str(exc), extra={"lfm_error_details": exc.details})
        except AnsibleTimeoutError as exc:
            logger.error(str(exc), extra={"lfm_error_details": exc.details})
            console.print(
                Panel(
                    f"[bold yellow]{exc.message}[/bold yellow]",
                    title="Timeout",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=3) from exc
        except AnsibleError as exc:
            logger.error(str(exc), extra={"lfm_error_details": exc.details})
            console.print(
                Panel(
                    f"[bold red]{exc.message}[/bold red]\n"
                    f"[dim]Verifique se o Ansible está instalado (pip install ansible) "
                    "e se o inventário contém hosts.[/dim]",
                    title="Erro de Execução Ansible",
                    border_style="red",
                )
            )
            raise typer.Exit(code=4) from exc
        total_s = time.perf_counter() - t0

    console.print()
    console.print("[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]Status dos hosts[/dim]")
    console.print("─" * 50)

    if not results:
        console.print()
        console.print(
            Panel(
                "[yellow]Nenhum resultado retornado pelo Ansible.[/yellow]\n"
                "[dim]Verifique se existem hosts descomentados no arquivo "
                "[bold]ansible/inventory/hosts.ini[/bold] e se a chave SSH "
                "foi copiada para os alvos.[/dim]",
                title="Sem Resultados",
                border_style="yellow",
            )
        )
        console.print(
            f"\n[dim]Duração total: [bold]{total_s:.2f}s[/bold][/dim]"
        )
        return

    table = Table()
    table.add_column("HOST", style="cyan")
    table.add_column("STATUS", style="bold", justify="center")
    table.add_column("LATÊNCIA", justify="right")
    table.add_column("OBS", style="dim")

    online = 0
    offline = 0
    auth = 0
    timeout = 0

    for r in results:
        style = _STATUS_STYLE.get(r.status, "bold")
        status_cell = f"[{style}]{r.status.value}[/{style}]"
        latency_cell = (
            f"[green]{r.latency_ms} ms[/green]"
            if r.latency_ms is not None
            else "[dim]—[/dim]"
        )
        obs_cell = r.error_message or ""
        if len(obs_cell) > 60:
            obs_cell = obs_cell[:57] + "..."
        table.add_row(r.host.name, status_cell, latency_cell, obs_cell)

        if r.status == HostStatusEnum.ONLINE:
            online += 1
        elif r.status == HostStatusEnum.OFFLINE:
            offline += 1
        elif r.status == HostStatusEnum.AUTH_ERROR:
            auth += 1
        elif r.status == HostStatusEnum.TIMEOUT:
            timeout += 1
        else:
            offline += 1

    console.print(table)
    console.print()
    total = len(results)
    console.print(
        f"[bold]Total:[/bold] {total}  ·  "
        f"[bold green]Online:[/bold green] {online}  ·  "
        f"[bold red]Offline:[/bold red] {offline}"
        + (f"  ·  [bold magenta]Auth.: {auth}[/bold magenta]" if auth else "")
        + (f"  ·  [bold yellow]Timeout: {timeout}[/bold yellow]" if timeout else "")
        + f"  ·  [dim]{total_s:.2f}s[/dim]"
    )

    if online == 0 and total > 0:
        console.print()
        console.print(
            Panel(
                "[yellow]Nenhum host respondeu ao ping.[/yellow]\n\n"
                "[dim]Possíveis causas:\n"
                "• SSH server não está rodando nos hosts alvo\n"
                "• Chave SSH não foi copiada (execute `ssh-copy-id <host>`)\n"
                "• Host está desligado ou inacessível na rede\n"
                "• Usuário ou porta incorretos no arquivo de inventário[/dim]",
                title="Diagnóstico",
                border_style="yellow",
            )
        )
