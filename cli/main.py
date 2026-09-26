from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from cli.commands.health import app as health_app
from cli.commands.exec import app as exec_app
from cli.commands.history import app as history_app
from cli.commands.inventory import app as inventory_app
from cli.commands.maintenance import app as maintenance_app
from cli.commands.packages import install_app, remove_app
from cli.commands.report import app as report_app
from cli.commands.status import app as status_app
from cli.commands.update import app as update_app
from core.config import get_settings
from core.exceptions import LFMError
from core.logger import get_logger

logger = get_logger("cli.main")

settings = get_settings()

app = typer.Typer(
    name="lfm",
    help=f"{settings.project_name} - Gerenciamento profissional de frota Linux.",
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(inventory_app, name="inventory", help="Gerenciar e visualizar inventário de hosts.")
app.add_typer(status_app, name="status", help="Verificar status de conectividade dos hosts.")
app.add_typer(health_app, name="health", help="Executar health checks nos hosts.")
app.add_typer(exec_app, name="exec", help="Executar comandos shell arbitrários nos hosts.")
app.add_typer(history_app, name="history", help="Visualizar histórico de operações.")
app.add_typer(report_app, name="report", help="Gerar relatórios consolidados da frota.")
app.add_typer(install_app, name="install", help="Instalar pacote nos hosts Linux.")
app.add_typer(remove_app, name="remove", help="Remover pacote dos hosts Linux.")
app.add_typer(update_app, name="update", help="Atualizar pacotes do sistema nos hosts Linux.")
app.add_typer(maintenance_app, name="maintenance", help="Executar ciclo de manutenção nos hosts Linux.")


def _version_callback(value: bool) -> None:
    if value:
        name = f"[bold cyan]{settings.project_name}[/bold cyan]"
        ver = f"[bold green]{settings.version}[/bold green]"
        rprint(f"{name} v{ver}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Exibe a versão do LFM e sai.",
        callback=_version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Aumenta o nível de detalhe dos logs (DEBUG).",
        is_eager=True,
    ),
    config_file: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Caminho para arquivo de configuração YAML alternativo.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """[bold]Linux Fleet Manager[/bold] — ferramenta de automação e gerenciamento de frota Linux."""
    if verbose:
        import logging
        logging.getLogger("lfm").setLevel(logging.DEBUG)
        logger.debug("Modo verbose ativado (nível DEBUG)")
    logger.debug(
        "LFM inicializado",
        extra={
            "lfm_version": settings.version,
            "lfm_base_dir": str(settings.base_dir),
            "lfm_config_file": str(config_file) if config_file else "default",
        },
    )


@app.command("version")
def version_cmd() -> None:
    """Exibe informações detalhadas sobre a versão e ambiente."""
    s = get_settings()
    rprint(f"[bold cyan]{s.project_name}[/bold cyan]")
    rprint(f"  Versão     : [bold]{s.version}[/bold]")
    rprint(f"  Python     : [dim]{__import__('sys').version.split()[0]}[/dim]")
    rprint(f"  Diretório  : [dim]{s.base_dir}[/dim]")
    rprint(f"  Logs       : [dim]{s.logs_dir}[/dim]")
    rprint(f"  Relatórios : [dim]{s.reports_dir}[/dim]")


def _error_handler(exc: LFMError, code: int = 1) -> None:
    logger.error(str(exc), extra={"lfm_error_details": exc.details})
    rprint(f"[bold red]Erro:[/bold red] {exc.message}")
    if exc.details:
        for k, v in exc.details.items():
            rprint(f"  [dim]{k}[/dim]: {v}")
    raise typer.Exit(code=code)


if __name__ == "__main__":
    try:
        app()
    except LFMError as e:
        _error_handler(e)
