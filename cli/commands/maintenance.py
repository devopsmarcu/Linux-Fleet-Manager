"""cli/commands/maintenance.py — Comando `lfm maintenance`.

Exibe o relatório de manutenção de cada host com 8 verificações:
SSH, Disco, Memória, Serviços, Atualizações, Cache, Temp, Orphans.

Responsabilidades:
- Receber opções (--host, --group, --clean, --yes, --json, --timeout).
- Delegar execução ao MaintenanceRunner (core/maintenance.py).
- Exibir painel detalhado por host com Rich.
- Exit code automação-friendly.
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.logger import get_logger
from core.maintenance import MaintenanceRunner
from core.models import (
    MaintenanceCheckState,
    MaintenanceHostReport,
    MaintenanceReport,
)

logger = get_logger("cli.commands.maintenance")

app = typer.Typer(
    name="maintenance",
    help="Executar ciclo completo de manutenção nos hosts Linux.",
    no_args_is_help=False,
)

_CONSOLE = Console()

_STATE_STYLE: dict[MaintenanceCheckState, str] = {
    MaintenanceCheckState.OK: "bold green",
    MaintenanceCheckState.WARNING: "bold yellow",
    MaintenanceCheckState.CRITICAL: "bold red",
    MaintenanceCheckState.SKIPPED: "dim",
}

_STATE_ICON: dict[MaintenanceCheckState, str] = {
    MaintenanceCheckState.OK: "✓",
    MaintenanceCheckState.WARNING: "⚠",
    MaintenanceCheckState.CRITICAL: "✗",
    MaintenanceCheckState.SKIPPED: "-",
}


@app.callback(invoke_without_command=True)
def maintenance_root(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", "-H", help="Host específico do inventário."),
    group: str | None = typer.Option(None, "--group", "-g", help="Grupo do inventário."),
    clean: bool = typer.Option(
        False, "--clean",
        help="Limpar cache de pacotes (apt autoclean / dnf clean) após coleta.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar (necessário com --clean)."),
    as_json: bool = typer.Option(False, "--json", help="Retornar em JSON válido."),
    timeout: int = typer.Option(
        60, "--timeout", "-t", help="Timeout SSH por host (segundos).", min=10, max=1800
    ),
) -> None:
    """Ciclo completo de manutenção: health, disco, memória, serviços, atualizações, cache e temp.

    Por padrão, apenas coleta e informa — sem modificar o sistema.
    Use --clean para executar limpeza de cache de pacotes.
    """
    if ctx.invoked_subcommand is not None:
        return

    _run_maintenance(
        host_name=host,
        group=group,
        clean=clean,
        yes=yes,
        as_json=as_json,
        timeout=timeout,
    )


def _run_maintenance(
    host_name: str | None = None,
    group: str | None = None,
    clean: bool = False,
    yes: bool = False,
    as_json: bool = False,
    timeout: int = 60,
) -> None:
    if not as_json:
        _CONSOLE.print()
        _CONSOLE.print("[bold cyan]Linux Fleet Manager[/bold cyan] · [dim]Maintenance[/dim]")
        _CONSOLE.print("─" * 50)
        if clean:
            _CONSOLE.print("  [bold yellow]Modo --clean ativado:[/bold yellow] o cache de pacotes será limpo após coleta.")
            _CONSOLE.print()
            if not yes:
                confirm = typer.confirm(
                    "Confirmar limpeza de cache de pacotes nos hosts?",
                    default=False,
                )
                if not confirm:
                    _CONSOLE.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
                    raise typer.Exit(code=0)

    runner = MaintenanceRunner()
    report: MaintenanceReport = runner.run_maintenance(
        host_name=host_name,
        group=group,
        clean=clean,
        timeout=timeout,
    )

    if as_json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
        _exit_by_report(report)
        return

    _print_report(report)
    _exit_by_report(report)


def _print_report(report: MaintenanceReport) -> None:
    """Imprime relatório detalhado de manutenção por host."""
    _CONSOLE.print()
    _CONSOLE.print(Panel(
        Text("Maintenance Report", style="bold white"),
        border_style="cyan",
        expand=False,
    ))

    if not report.host_reports:
        _CONSOLE.print("[yellow]Nenhum host encontrado.[/yellow]")
        return

    for rep in report.host_reports:
        _print_host_panel(rep)

    # Sumário final
    _CONSOLE.print()
    _CONSOLE.print("─" * 50)
    _CONSOLE.print(
        f"[bold]Total:[/bold] {report.total}  ·  "
        f"[bold green]OK:[/bold green] {report.healthy_count}  ·  "
        f"[bold yellow]Warning:[/bold yellow] {report.warning_count}  ·  "
        f"[bold red]Critical:[/bold red] {report.critical_count}"
    )
    _CONSOLE.print(f"[dim]Duração: {report.duration_seconds:.1f}s[/dim]")


def _print_host_panel(rep: MaintenanceHostReport) -> None:
    """Imprime painel de manutenção de um único host."""
    overall_style = _STATE_STYLE.get(rep.overall_state, "bold")
    overall_icon = _STATE_ICON.get(rep.overall_state, "?")

    _CONSOLE.print()
    _CONSOLE.print(
        f"[bold cyan]{rep.host}[/bold cyan]"
        + (f"  [dim]({rep.address})[/dim]" if rep.address and rep.address != rep.host else "")
        + (f"  [dim]— {rep.distribution}[/dim]" if rep.distribution else "")
    )
    _CONSOLE.print("─" * 40)

    if rep.error and not rep.checks:
        # Host inalcançável — mostrar apenas o erro
        _CONSOLE.print(f"  [{overall_style}]{overall_icon} {rep.error}[/{overall_style}]")
    else:
        # Tabela de checks
        table = Table(show_header=False, show_lines=False, box=None, padding=(0, 2))
        table.add_column("check", style="dim", width=14)
        table.add_column("value", style="white")

        for check in rep.checks:
            style = _STATE_STYLE.get(check.state, "dim")
            icon = _STATE_ICON.get(check.state, " ")
            value_text = check.value or "-"
            table.add_row(
                check.name,
                f"[{style}]{icon}  {value_text}[/{style}]",
            )

        _CONSOLE.print(table)

        # Linha extra com métricas adicionais
        extras = []
        if rep.orphan_packages > 0:
            extras.append(f"Orphans: {rep.orphan_packages} pkgs")
        if rep.failed_services:
            extras.append(f"Failed svcs: {', '.join(rep.failed_services[:2])}")
        if extras:
            _CONSOLE.print(f"  [dim]{' · '.join(extras)}[/dim]")

    _CONSOLE.print(
        f"\n  Status: [{overall_style}]{rep.overall_state.value}[/{overall_style}]"
    )


def _exit_by_report(report: MaintenanceReport) -> None:
    """Exit codes: 0=OK, 1=warnings, 2=critical."""
    if report.critical_count > 0:
        raise typer.Exit(code=2)
    if report.warning_count > 0:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)
