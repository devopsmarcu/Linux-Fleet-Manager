"""cli/commands/update.py — Comando `lfm update`.

Responsabilidades:
- Receber opções do usuário (--host, --group, --yes, --json, --timeout).
- Mostrar hosts-alvo antes de executar e pedir confirmação.
- Delegar toda lógica ao SystemUpdater (core/updater.py).
- Exibir resultado tabular com Rich.
- Exit code automação-friendly.
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from core.inventory import InventoryManager
from core.logger import get_logger
from core.models import UpdateReport
from core.updater import SystemUpdater

logger = get_logger("cli.commands.update")

app = typer.Typer(
    name="update",
    help="Atualizar pacotes do sistema nos hosts Linux.",
    no_args_is_help=False,
)

_CONSOLE = Console()


@app.callback(invoke_without_command=True)
def update_root(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico do inventário."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo do inventário."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON válido."),
    timeout: int = typer.Option(120, "--timeout", "-t", help="Timeout SSH por host (segundos).", min=10, max=1800),
) -> None:
    """Atualizar o sistema operacional nos hosts Linux via Ansible.

    Usa módulos nativos (apt/dnf/pacman) — não executa shell bruto.
    Operação idempotente: aplica apenas o que for necessário.
    """
    if ctx.invoked_subcommand is not None:
        return

    _run_update(host_name=host, group=group, yes=yes, as_json=as_json, timeout=timeout)


def _run_update(
    host_name: str | None = None,
    group: str | None = None,
    yes: bool = False,
    as_json: bool = False,
    timeout: int = 120,
) -> None:
    # Resolver hosts-alvo
    try:
        inv = InventoryManager()
        targets = inv.list_hosts(group=group, host_name=host_name)
    except Exception as exc:
        _CONSOLE.print(f"[bold red]Erro ao carregar inventário:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if not targets:
        _CONSOLE.print("[yellow]Nenhum host encontrado para o alvo especificado.[/yellow]")
        raise typer.Exit(code=0)

    if not as_json:
        _CONSOLE.print()
        _CONSOLE.print("[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]System Update[/dim]")
        _CONSOLE.print("─" * 50)
        _CONSOLE.print(f"  Targets : [bold]{len(targets)}[/bold] host(s)")
        _CONSOLE.print()
        for h in targets:
            _CONSOLE.print(f"  [dim]→[/dim] {h.name}  [dim]({h.address})[/dim]")
        _CONSOLE.print()
        _CONSOLE.print(
            "  [dim]Modo safe: atualiza pacotes disponíveis sem remover ou fazer "
            "dist-upgrade.[/dim]"
        )
        _CONSOLE.print()

        if not yes:
            confirm = typer.confirm(
                f"Confirmar atualização em {len(targets)} host(s)?",
                default=False,
            )
            if not confirm:
                _CONSOLE.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
                raise typer.Exit(code=0)

    updater = SystemUpdater()
    report: UpdateReport = updater.run_update(
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


def _print_report(report: UpdateReport) -> None:
    table = Table(show_lines=False)
    table.add_column("HOST", style="cyan", no_wrap=True)
    table.add_column("STATUS", style="bold", justify="center")
    table.add_column("CHANGED", justify="center")
    table.add_column("PKGS", justify="right")
    table.add_column("PKG MANAGER", style="dim")
    table.add_column("DISTRO", style="dim")
    table.add_column("INFO", style="dim")

    for r in report.results:
        status_cell = "[bold green][OK][/bold green]" if r.success else "[bold red][FAIL][/bold red]"
        changed_cell = "[green]yes[/green]" if r.changed else "no"
        pkgs = str(r.packages_updated) if r.packages_updated > 0 else "-"
        pkg_mgr = r.pkg_manager or "-"
        distro = r.distribution or "-"
        info = r.error or ("-" if r.changed else "already up-to-date")
        if len(info) > 50:
            info = info[:47] + "..."
        table.add_row(r.host, status_cell, changed_cell, pkgs, pkg_mgr, distro, info)

    _CONSOLE.print(table)
    _CONSOLE.print()
    _CONSOLE.print(
        f"[bold]Targets:[/bold] {report.total}  ·  "
        f"[bold green]Success:[/bold green] {report.success_count}  ·  "
        f"[bold red]Failed:[/bold red] {report.failed_count}  ·  "
        f"[bold]Updated:[/bold] {report.changed_count} host(s)  ·  "
        f"[bold]Packages:[/bold] {report.total_packages_updated}"
    )
    _CONSOLE.print(
        f"[dim]Duração total: {report.duration_seconds:.1f}s[/dim]"
    )


def _exit_by_report(report: UpdateReport) -> None:
    raise typer.Exit(code=2 if report.failed_count > 0 else 0)
