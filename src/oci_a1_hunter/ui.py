"""Console helpers shared by the CLI and the setup wizard."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import IntPrompt

from oci_a1_hunter.i18n import t

console = Console(highlight=False)


def die(message: str, code: int = 1) -> typer.Exit:
    console.print(f"[bold red]{t('Error', 'Ошибка')}:[/] {message}")
    return typer.Exit(code)


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def choose(title: str, options: list[str], default: int = 1) -> int:
    """Numbered menu. Returns a 0-based index."""
    console.print(f"\n[bold]{title}[/]")
    for n, label in enumerate(options, 1):
        console.print(f"  [cyan]{n}[/]) {label}")
    while True:
        n = IntPrompt.ask(t("Choose", "Выбери"), default=default)
        if 1 <= n <= len(options):
            return n - 1
        console.print(f"[red]{t('Pick a number from the list', 'Выбери номер из списка')}[/]")


def step(n: int, total: int, title: str) -> None:
    console.rule(f"[bold]{n}/{total} · {title}[/]", align="left")
