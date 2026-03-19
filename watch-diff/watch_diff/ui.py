from __future__ import annotations

import difflib
import queue
import random
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# ── constants ────────────────────────────────────────────────────────────────

_SPINNER = list("⣾⣽⣻⢿⡿⣟⣯⣷")
_STREAM_DELAY_MIN = 0.020
_STREAM_DELAY_MAX = 0.045

# colour palette
_C_ADD_FG = "#66ff99"
_C_ADD_BG = "#002210"
_C_REM_FG = "#ff6666"
_C_REM_BG = "#220000"
_C_HUNK = "bold bright_blue"
_C_HEADER = "dim white"
_C_PANEL_BG = "#0d0d14"
_C_STATUS_BG = "#0d1a2e"


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class _DiffLine:
    kind: str   # 'add' | 'remove' | 'hunk' | 'header' | 'context'
    text: str


@dataclass
class _State:
    filename: str = ""
    filepath: str = ""
    adds: int = 0
    removes: int = 0
    is_streaming: bool = False
    diff_lines: list[_DiffLine] = field(default_factory=list)
    full_file_content: str = ""
    full_file_path: str = ""
    history: list[str] = field(default_factory=list)
    timestamp: str = ""
    total_diff_lines: int = 0
    streamed_diff_lines: int = 0
    spinner_frame: int = 0


# ── UI ────────────────────────────────────────────────────────────────────────

class DiffUI:
    def __init__(self) -> None:
        self._state = _State()
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._shutdown = threading.Event()

    # public API ──────────────────────────────────────────────────────────────

    def on_file_changed(
        self, path: str, old_lines: list[str], new_lines: list[str]
    ) -> None:
        self._queue.put(("change", path, old_lines, new_lines))

    def run(self) -> None:
        worker = threading.Thread(target=self._stream_worker, daemon=True)
        worker.start()
        layout = self._make_skeleton()
        try:
            with Live(layout, refresh_per_second=20, screen=True, console=Console()) as live:
                frame = 0
                while not self._shutdown.is_set():
                    with self._lock:
                        self._state.spinner_frame = frame
                        self._fill_layout(self._state, layout)
                    live.update(layout)
                    time.sleep(0.05)
                    frame += 1
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown.set()

    def stop(self) -> None:
        self._shutdown.set()

    # layout skeleton ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_skeleton() -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="status", size=3),
            Layout(name="progress", size=3),
            Layout(name="diff", ratio=2),
            Layout(name="file", ratio=1),
            Layout(name="history", size=3),
        )
        return layout

    # layout fill ─────────────────────────────────────────────────────────────

    def _fill_layout(self, state: _State, layout: Layout) -> None:
        layout["status"].update(self._render_status(state))
        layout["progress"].update(self._render_progress(state))
        layout["diff"].update(self._render_diff(state))
        layout["file"].update(self._render_file(state))
        layout["history"].update(self._render_history(state))

    # ── status panel ─────────────────────────────────────────────────────────

    @staticmethod
    def _render_status(state: _State) -> Panel:
        t = Text()
        if state.filename:
            t.append(f"  {state.filename}  ", style="bold white")
            t.append(f" +{state.adds} ", style=f"bold {_C_ADD_FG}")
            t.append(f" -{state.removes} ", style=f"bold {_C_REM_FG}")
            if state.is_streaming:
                frame = _SPINNER[state.spinner_frame % len(_SPINNER)]
                t.append(f"  {frame} streaming…", style="bold yellow")
            elif state.timestamp:
                t.append(f"  ✓ {state.timestamp}", style="dim green")
        else:
            frame = _SPINNER[state.spinner_frame % len(_SPINNER)]
            t.append(f"  {frame} Watching for changes…", style="dim yellow")

        return Panel(
            t,
            title="[bold bright_white]watch-diff[/bold bright_white]",
            style=f"on {_C_STATUS_BG}",
            border_style="bright_blue",
        )

    # ── progress panel ────────────────────────────────────────────────────────

    @staticmethod
    def _render_progress(state: _State) -> Panel:
        if state.total_diff_lines > 0 and state.is_streaming:
            pct = state.streamed_diff_lines / state.total_diff_lines
            bar_w = 52
            filled = int(bar_w * pct)
            t = Text()
            t.append("  ")
            t.append("█" * filled, style="bright_cyan")
            t.append("░" * (bar_w - filled), style="dim")
            t.append(f"  {pct * 100:.0f}%  ", style="cyan")
            return Panel(t, style=f"on {_C_STATUS_BG}", border_style="cyan")
        elif state.total_diff_lines > 0 and not state.is_streaming:
            # fully streamed — solid bar
            t = Text()
            t.append("  ")
            t.append("█" * 52, style="green")
            t.append("  100%  ", style="green")
            return Panel(t, style=f"on {_C_STATUS_BG}", border_style="dim green")
        else:
            return Panel(
                Text("  Idle", style="dim"),
                style=f"on {_C_STATUS_BG}",
                border_style="dim",
            )

    # ── diff panel ────────────────────────────────────────────────────────────

    @staticmethod
    def _diff_visible_lines() -> int:
        """How many diff lines can fit in the panel (best-effort estimate)."""
        rows = shutil.get_terminal_size((80, 40)).lines
        # fixed panels: status(3) + progress(3) + history(3) = 9
        # panel borders add ~3 lines total overhead
        flex = max(12, rows - 12)
        # diff gets ratio=2 out of ratio=3 total flex rows
        return max(6, flex * 2 // 3 - 2)

    def _render_diff(self, state: _State) -> Panel:
        visible = self._diff_visible_lines()
        lines_to_show = state.diff_lines[-visible:]

        t = Text(no_wrap=True, overflow="fold")
        for dl in lines_to_show:
            line = dl.text.rstrip("\n")
            if dl.kind == "add":
                t.append(line + "\n", style=f"{_C_ADD_FG} on {_C_ADD_BG}")
            elif dl.kind == "remove":
                t.append(line + "\n", style=f"{_C_REM_FG} on {_C_REM_BG}")
            elif dl.kind == "hunk":
                t.append(line + "\n", style=_C_HUNK)
            elif dl.kind == "header":
                t.append(line + "\n", style=_C_HEADER)
            else:
                t.append(line + "\n", style="white")

        title = "[bold green]diff[/bold green]"
        if state.filename:
            title += f" — [dim]{state.filename}[/dim]"

        return Panel(
            t,
            title=title,
            style=f"on {_C_PANEL_BG}",
            border_style="green",
        )

    # ── file panel ────────────────────────────────────────────────────────────

    @staticmethod
    def _render_file(state: _State) -> Panel:
        if state.full_file_path and state.full_file_content:
            ext = Path(state.full_file_path).suffix.lstrip(".") or "text"
            try:
                content = Syntax(
                    state.full_file_content,
                    ext,
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=False,
                )
            except Exception:
                content = Text(state.full_file_content)

            return Panel(
                content,
                title=f"[bold cyan]{state.full_file_path}[/bold cyan]",
                style=f"on {_C_PANEL_BG}",
                border_style="cyan",
            )

        return Panel(
            Text("  No file viewed yet", style="dim"),
            title="[bold]file[/bold]",
            style=f"on {_C_PANEL_BG}",
            border_style="dim",
        )

    # ── history panel ─────────────────────────────────────────────────────────

    @staticmethod
    def _render_history(state: _State) -> Panel:
        t = Text()
        if state.history:
            for h in state.history[-15:]:
                short = Path(h).name
                active = h == state.filepath or Path(h).name == state.filename
                style = "bold white on #003366" if active else "dim white on #1a1a2e"
                t.append(f" {short} ", style=style)
                t.append("  ")
        else:
            t.append("  No history yet", style="dim")

        return Panel(
            t,
            title="[bold]history[/bold]",
            style=f"on {_C_STATUS_BG}",
            border_style="bright_blue",
        )

    # ── stream worker ─────────────────────────────────────────────────────────

    def _stream_worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            _, path, old_lines, new_lines = event
            self._process_change(path, old_lines, new_lines)

    def _process_change(
        self, path: str, old_lines: list[str], new_lines: list[str]
    ) -> None:
        short = Path(path).name if len(path) > 50 else path

        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{short}",
                tofile=f"b/{short}",
                lineterm="",
            )
        )

        if not diff:
            return

        adds = sum(
            1 for ln in diff if ln.startswith("+") and not ln.startswith("+++")
        )
        removes = sum(
            1 for ln in diff if ln.startswith("-") and not ln.startswith("---")
        )

        # reset state for new stream
        with self._lock:
            self._state.filename = short
            self._state.filepath = path
            self._state.adds = adds
            self._state.removes = removes
            self._state.is_streaming = True
            self._state.diff_lines = []
            self._state.total_diff_lines = len(diff)
            self._state.streamed_diff_lines = 0

        # stream lines one by one
        for line in diff:
            if self._shutdown.is_set():
                return

            if line.startswith("@@"):
                kind = "hunk"
            elif line.startswith("+++") or line.startswith("---"):
                kind = "header"
            elif line.startswith("+"):
                kind = "add"
            elif line.startswith("-"):
                kind = "remove"
            else:
                kind = "context"

            with self._lock:
                self._state.diff_lines.append(_DiffLine(kind, line))
                self._state.streamed_diff_lines += 1

            time.sleep(random.uniform(_STREAM_DELAY_MIN, _STREAM_DELAY_MAX))

        # finalise
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._state.is_streaming = False
            self._state.timestamp = timestamp
            self._state.full_file_content = "".join(new_lines)
            self._state.full_file_path = path

            history = self._state.history
            if path in history:
                history.remove(path)
            history.append(path)
