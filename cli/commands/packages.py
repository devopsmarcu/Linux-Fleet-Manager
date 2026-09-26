"""cli/commands/packages.py — Comandos `lfm install` e `lfm remove`.

Responsabilidades:
- Receber argumentos e opções do usuário via Typer.
- Mostrar hosts-alvo antes de executar e pedir confirmação.
- Delegar toda lógica para PackageManager (core/packages.py).
- Exibir resultado formatado com Rich.
- Retornar exit code adequado para automação.
- Não conter lógica de negócio.
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import PackageAction, PackageReport
from core.packages import PackageManager, PackageValidationError

logger = get_logger("cli.commands.packages")

# ---------------------------------------------------------------------------
# Apps Typer — um por subcomando de primeiro nível
# ---------------------------------------------------------------------------

install_app = typer.Typer(
    name="install",
    help="Instalar pacote nos hosts Linux.",
    no_args_is_help=True,
)

remove_app = typer.Typer(
    name="remove",
    help="Remover pacote dos hosts Linux.",
    no_args_is_help=True,
)

_CONSOLE = Console()


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

@install_app.callback(invoke_without_command=True)
def install_root(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Nome do pacote a instalar (ex: htop)."),
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico do inventário."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo do inventário."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON válido."),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout SSH em segundos.", min=1, max=600),
) -> None:
    """Instalar um pacote Linux via Ansible (detecta pkg manager automaticamente)."""
    if ctx.invoked_subcommand is not None:
        return
    _run_package_operation(
        package=package,
        action=PackageAction.INSTALL,
        host_name=host,
        group=group,
        yes=yes,
        as_json=as_json,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

@remove_app.callback(invoke_without_command=True)
def remove_root(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Nome do pacote a remover (ex: nginx)."),
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico do inventário."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo do inventário."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON válido."),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout SSH em segundos.", min=1, max=600),
) -> None:
    """Remover um pacote Linux via Ansible."""
    if ctx.invoked_subcommand is not None:
        return
    _run_package_operation(
        package=package,
        action=PackageAction.REMOVE,
        host_name=host,
        group=group,
        yes=yes,
        as_json=as_json,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Lógica compartilhada (sem apresentação misturada com negócio)
# ---------------------------------------------------------------------------

def _run_package_operation(
    package: str,
    action: PackageAction,
    host_name: str | None = None,
    group: str | None = None,
    yes: bool = False,
    as_json: bool = False,
    timeout: int = 30,
) -> None:
    # 1. Validação antecipada do nome de pacote
    try:
        PackageManager.validate_package_name(package)
    except PackageValidationError as exc:
        _CONSOLE.print(f"[bold red]Erro de validação:[/bold red] {exc.message}")
        raise typer.Exit(code=1) from exc

    # 2. Resolver e exibir hosts antes da execução
    try:
        inv = InventoryManager()
        targets = inv.list_hosts(group=group, host_name=host_name)
    except Exception as exc:
        _CONSOLE.print(f"[bold red]Erro ao carregar inventário:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if not targets:
        _CONSOLE.print("[yellow]Nenhum host encontrado para o alvo especificado.[/yellow]")
        raise typer.Exit(code=0)

    action_label = "Instalar" if action == PackageAction.INSTALL else "Remover"
    target_pattern = host_name or group or "all"

    if not as_json:
        _CONSOLE.print()
        _CONSOLE.print(f"[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]Package {action_label}[/dim]")
        _CONSOLE.print("─" * 50)
        _CONSOLE.print(f"  Pacote  : [bold]{package}[/bold]")
        _CONSOLE.print(f"  Ação    : [bold]{'instalar' if action == PackageAction.INSTALL else 'remover'}[/bold]")
        _CONSOLE.print(f"  Targets : [bold]{len(targets)}[/bold] host(s)")
        _CONSOLE.print()
        for h in targets:
            _CONSOLE.print(f"  [dim]→[/dim] {h.name}  [dim]({h.address})[/dim]")
        _CONSOLE.print()

        # 3. Confirmação interativa (pular se --yes ou --json)
        if not yes:
            confirm = typer.confirm(
                f"Confirmar {'instalação' if action == PackageAction.INSTALL else 'remoção'} de '{package}' em {len(targets)} host(s)?",
                default=False,
            )
            if not confirm:
                _CONSOLE.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
                raise typer.Exit(code=0)

    # 4. Executar
    manager = PackageManager()
    report: PackageReport = manager._run_operation(
        package=package,
        action=action,
        host_name=host_name,
        group=group,
        timeout=timeout,
    )

    # 5. Saída
    if as_json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
        _exit_by_report(report)
        return

    _print_report(report)
    _exit_by_report(report)


def _print_report(report: PackageReport) -> None:
    """Exibe resultado tabular do relatório de pacotes."""
    table = Table(show_lines=False)
    table.add_column("HOST", style="cyan", no_wrap=True)
    table.add_column("STATUS", style="bold", justify="center")
    table.add_column("CHANGED", justify="center")
    table.add_column("PKG MANAGER", style="dim")
    table.add_column("INFO", style="dim")

    for r in report.results:
        if r.success:
            status_cell = "[bold green][OK][/bold green]"
        else:
            status_cell = "[bold red][FAIL][/bold red]"

        changed_cell = "[green]yes[/green]" if r.changed else "no"
        pkg_mgr = r.pkg_manager or "-"
        info = r.error or r.msg or "-"
        # Truncar info longa
        if len(info) > 60:
            info = info[:57] + "..."

        table.add_row(r.host, status_cell, changed_cell, pkg_mgr, info)

    _CONSOLE.print(table)
    _CONSOLE.print()

    action_verb = "instalado" if report.action == PackageAction.INSTALL else "removido"
    _CONSOLE.print(
        f"[bold]Package:[/bold] {report.package}  ·  "
        f"[bold]Targets:[/bold] {report.total}  ·  "
        f"[bold green]Success:[/bold green] {report.success_count}  ·  "
        f"[bold red]Failed:[/bold red] {report.failed_count}"
    )
    if report.changed_count > 0:
        _CONSOLE.print(f"[dim]Pacote {action_verb} em {report.changed_count} host(s).[/dim]")
    elif report.success_count > 0 and report.failed_count == 0:
        _CONSOLE.print(f"[dim]Nenhuma mudança necessária (já {'instalado' if report.action == PackageAction.INSTALL else 'ausente'}).[/dim]")


def _exit_by_report(report: PackageReport) -> None:
    """Exit code para automação: 0=sucesso, 2=falhas parciais/totais."""
    if report.failed_count > 0:
        raise typer.Exit(code=2)
    raise typer.Exit(code=0)
