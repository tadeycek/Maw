from __future__ import annotations

import sys
from pathlib import Path

import click

from .ui import DiffUI
from .watcher import start_watcher


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("path", default=".", metavar="PATH")
@click.option(
    "--ext",
    multiple=True,
    metavar="EXT",
    help="Only watch files with these extensions, e.g. .py .ts  (repeatable)",
)
@click.option(
    "--ignore",
    multiple=True,
    metavar="PATTERN",
    help="Ignore paths containing this substring, e.g. tests/  (repeatable)",
)
def main(path: str, ext: tuple[str, ...], ignore: tuple[str, ...]) -> None:
    """Watch PATH for file changes and stream unified diffs in a rich terminal UI.

    \b
    Examples:
      watch-diff ./src              # watch whole directory
      watch-diff agent/foo.py       # watch single file
      watch-diff . --ext .py .ts    # filter by extension
      watch-diff . --ignore tests/  # exclude path pattern
    """
    resolved = Path(path).resolve()
    if not resolved.exists():
        click.echo(f"error: '{path}' does not exist", err=True)
        sys.exit(1)

    # normalise extensions so both ".py" and "py" work
    ext_normalised = tuple(
        f".{e.lstrip('.')}" for e in ext
    )

    # pre-populate file store for single-file targets
    file_store: dict[str, list[str]] = {}
    if resolved.is_file():
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            file_store[str(resolved)] = content.splitlines(keepends=True)
        except OSError:
            pass

    ui = DiffUI()
    observer = start_watcher(str(resolved), file_store, ui.on_file_changed, ext_normalised, ignore)

    try:
        ui.run()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
