"""cli/commands/packages.py — Gerenciamento de pacotes via `lfm package`.
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

app = typer.Typer(
    name="package",
    help="Gerenciar pacotes nos hosts Linux.",
    no_args_is_help=True,
)

_CONSOLE = Console()

def _run_package_operation(
    action: PackageAction,
    package: str,
    host_name: str | None = None,
    group: str | None = None,
    yes: bool = False,
    as_json: bool = False,
    timeout: int = 30,
) -> None:
    try:
        PackageManager.validate_package_name(package)
    except PackageValidationError as exc:
        _CONSOLE.print(f"[bold red]Erro de validação:[/bold red] {exc.message}")
        raise typer.Exit(code=1) from exc

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

        if not yes:
            confirm = typer.confirm(
                f"Confirmar {'instalação' if action == PackageAction.INSTALL else 'remoção'} de '{package}' em {len(targets)} host(s)?",
                default=False,
            )
            if not confirm:
                _CONSOLE.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
                raise typer.Exit(code=0)

    manager = PackageManager()
    report: PackageReport = manager._run_operation(
        package=package,
        action=action,
        host_name=host_name,
        group=group,
        timeout=timeout,
    )

    if as_json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
        _exit_by_report(report)
        return

    _print_report(report)
    _exit_by_report(report)

def _print_report(report: PackageReport) -> None:
    table = Table(show_lines=False)
    table.add_column("HOST", style="cyan", no_wrap=True)
    table.add_column("STATUS", style="bold", justify="center")
    table.add_column("CHANGED", justify="center")
    table.add_column("PKG MANAGER", style="dim")
    table.add_column("INFO", style="dim")

    for r in report.results:
        status_cell = "[bold green][OK][/bold green]" if r.success else "[bold red][FAIL][/bold red]"
        changed_cell = "[green]yes[/green]" if r.changed else "no"
        pkg_mgr = r.pkg_manager or "-"
        info = r.error or r.msg or "-"
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
    if report.failed_count > 0:
        raise typer.Exit(code=2)
    raise typer.Exit(code=0)

@app.command("install")
def install(
    package: str = typer.Argument(..., help="Nome do pacote a instalar."),
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo específico."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON."),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout SSH.", min=1, max=600),
) -> None:
    """Instalar um pacote Linux via Ansible."""
    _run_package_operation(PackageAction.INSTALL, package, host, group, yes, as_json, timeout)

@app.command("remove")
def remove(
    package: str = typer.Argument(..., help="Nome do pacote a remover."),
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo específico."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON."),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout SSH.", min=1, max=600),
) -> None:
    """Remover um pacote Linux via Ansible."""
    _run_package_operation(PackageAction.REMOVE, package, host, group, yes, as_json, timeout)
