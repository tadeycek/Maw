from __future__ import annotations

from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _ChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        file_store: dict[str, list[str]],
        callback: Callable[[str, list[str], list[str]], None],
        watch_target: Path,
        ext_filter: set[str],
        ignore_patterns: list[str],
    ) -> None:
        super().__init__()
        self.file_store = file_store
        self.callback = callback
        self.watch_target = watch_target.resolve()
        self.is_single_file = self.watch_target.is_file()
        self.ext_filter = ext_filter
        self.ignore_patterns = ignore_patterns

    def _should_process(self, src_path: str) -> bool:
        path = Path(src_path).resolve()
        if self.is_single_file:
            return path == self.watch_target
        if self.ext_filter and path.suffix not in self.ext_filter:
            return False
        for pattern in self.ignore_patterns:
            if pattern in str(path):
                return False
        return True

    def _handle(self, src_path: str) -> None:
        if not self._should_process(src_path):
            return
        try:
            with open(src_path, encoding="utf-8", errors="replace") as f:
                new_content = f.read()
        except OSError:
            return

        new_lines = new_content.splitlines(keepends=True)
        old_lines = self.file_store.get(src_path, [])

        if new_lines == old_lines:
            return

        self.file_store[src_path] = list(new_lines)
        self.callback(src_path, list(old_lines), list(new_lines))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)


def start_watcher(
    path: str,
    file_store: dict[str, list[str]],
    callback: Callable[[str, list[str], list[str]], None],
    ext_filter: tuple[str, ...],
    ignore_patterns: tuple[str, ...],
) -> Observer:
    watch_path = Path(path).resolve()
    is_file = watch_path.is_file()
    watch_dir = str(watch_path.parent if is_file else watch_path)

    handler = _ChangeHandler(
        file_store,
        callback,
        watch_path,
        set(ext_filter),
        list(ignore_patterns),
    )
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=not is_file)
    observer.start()
    return observer
