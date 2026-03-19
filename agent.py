#!/usr/bin/env python3
"""
Maw — a local AI file agent powered by Ollama.
Runs in any folder, manages files, runs commands, and remembers context.
"""

import difflib
import os
import sys
import json
import re
from typing import Optional
import shutil
import subprocess
import argparse
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PtStyle

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
MAW_DIR = Path(".maw")
HISTORY_FILE = MAW_DIR / "history.json"
MAX_TOOL_ITERATIONS = 10          # guard against infinite tool loops
MAX_FILE_CHARS = 4000             # truncate large files before sending to model
MODEL_TIMEOUT = 600               # seconds — increase for large/slow models (e.g. 30B on CPU)
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".sh", ".css", ".html", ".xml", ".csv",
    ".rs", ".go", ".c", ".cpp", ".h", ".java", ".rb", ".php",
}

console = Console()

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Maw, a local AI file agent. You help users manage files and run commands.

RULES:
- When you need to use a tool, respond with ONLY the raw JSON — no words before or after it.
- After a [Tool result] is given to you, use it to continue working or call the next tool.
- When ALL tasks are done and no more tools are needed, write a SHORT plain-text explanation of what you did. Never output raw JSON as your final message.
- Do NOT narrate what you are about to do — just do it, then explain after.
- [Context from local files] blocks are background information only. Do NOT use tools on them unless the user explicitly asks.

TOOLS:
{"action": "create_file", "filename": "name.txt", "content": "file content here"}
{"action": "read_file", "filename": "name.txt"}
{"action": "delete_file", "filename": "name.txt"}
{"action": "list_files"}
{"action": "edit_file", "filename": "name.txt", "find": "old text", "replace": "new text"}
{"action": "create_folder", "path": "folder/name"}
{"action": "move_file", "src": "old/path.txt", "dst": "new/path.txt"}
{"action": "run_command", "command": "ls -la"}

EXAMPLE — multi-step task:
user: read hello.txt and rewrite it in uppercase
→ {"action": "read_file", "filename": "hello.txt"}
[Tool result]: hello world
→ {"action": "create_file", "filename": "hello.txt", "content": "HELLO WORLD"}
[Tool result]: Created hello.txt
→ Rewrote hello.txt in uppercase.

EXAMPLE — greeting:
user: hey
→ Hey! What can I help you with?"""

# ── RAG Memory ────────────────────────────────────────────────────────────────
# Uses chromadb (vector store) + sentence-transformers (local embeddings).
# Falls back gracefully if these packages aren't installed.

_rag_enabled = False
_collection = None
_embed_model = None


def init_rag() -> None:
    """Set up the local vector memory. Called once at startup."""
    global _rag_enabled, _collection, _embed_model
    try:
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        import chromadb
        from sentence_transformers import SentenceTransformer

        MAW_DIR.mkdir(exist_ok=True)
        client = chromadb.PersistentClient(path=str(MAW_DIR / "chroma"))
        _collection = client.get_or_create_collection("files")
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stdout, saved_stderr = os.dup(1), os.dup(2)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        try:
            _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(devnull_fd)
            os.close(saved_stdout)
            os.close(saved_stderr)
        _rag_enabled = True

        _index_all_files()
    except ImportError:
        console.print(
            "[yellow]RAG disabled — install chromadb + sentence-transformers "
            "to enable per-folder memory.[/yellow]"
        )
    except Exception as e:
        console.print(f"[yellow]RAG disabled (error: {e})[/yellow]")


def _embed(text: str) -> list:
    return _embed_model.encode(text[:2000]).tolist()


def _is_indexable(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in TEXT_EXTENSIONS
        and path.stat().st_size < 500_000
    )


def _index_file(path: Path) -> None:
    """Add or update a single file in the vector store."""
    if not _rag_enabled:
        return
    try:
        content = path.read_text(errors="replace")
        doc_id = str(path.resolve())
        _collection.upsert(
            ids=[doc_id],
            embeddings=[_embed(content)],
            documents=[content[:2000]],
            metadatas=[{"filename": path.name}],
        )
    except Exception:
        pass


def _remove_from_index(path: str) -> None:
    if not _rag_enabled:
        return
    try:
        _collection.delete(ids=[str(Path(path).resolve())])
    except Exception:
        pass


def _index_all_files() -> None:
    """Index text files in the current directory (non-recursive, skips .maw/.venv)."""
    for path in Path(".").iterdir():
        if path.name in {".maw", ".venv", ".git", "node_modules"}:
            continue
        if _is_indexable(path):
            _index_file(path)


RAG_DISTANCE_THRESHOLD = 1.0  # chromadb cosine distance; lower = more similar

def memory_query(query: str, n: int = 3) -> str:
    """Return the most relevant file snippets for a query, or '' if RAG is off."""
    if not _rag_enabled or _collection.count() == 0:
        return ""
    try:
        results = _collection.query(
            query_embeddings=[_embed(query)],
            n_results=min(n, _collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        parts = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if dist <= RAG_DISTANCE_THRESHOLD:
                parts.append(f"[{meta['filename']}]:\n{doc[:500]}")
        return "\n\n".join(parts)
    except Exception:
        return ""


# ── Conversation history ──────────────────────────────────────────────────────
# Persisted to .maw/history.json so the session survives restarts.

history: list[dict] = []


def load_history() -> None:
    global history
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            history = []


def save_history() -> None:
    MAW_DIR.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# ── Diff renderer ─────────────────────────────────────────────────────────────

DIFF_SHOW_THRESHOLD = 30  # hide full diff when changed lines exceed this

def _print_diff(old_lines: list[str], new_lines: list[str], filename: str) -> None:
    """Render a unified diff inline in the conversation."""
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    ))
    if not diff:
        return

    adds = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removes = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    summary = Text()
    summary.append(f" +{adds} ", style="bold bright_green")
    summary.append(f"-{removes} ", style="bold bright_red")
    summary.append(filename, style="dim")

    if adds + removes >= DIFF_SHOW_THRESHOLD:
        # Too many changes — just print the summary line, skip the full diff.
        console.print(summary)
        return

    t = Text(no_wrap=True, overflow="fold")
    for line in diff:
        if line.startswith("@@"):
            t.append(line, style="bold bright_blue")
        elif line.startswith("+++") or line.startswith("---"):
            t.append(line, style="dim white")
        elif line.startswith("+"):
            t.append(line, style="bright_green on #002210")
        elif line.startswith("-"):
            t.append(line, style="bright_red on #1a0000")
        else:
            t.append(line, style="dim white")
        t.append("\n")

    console.print(Panel(t, title=summary, border_style="dim", padding=(0, 0)))


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_create_file(filename: str, content: str) -> str:
    path = Path(filename)
    old_lines = path.read_text(errors="replace").splitlines(keepends=True) if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _print_diff(old_lines, content.splitlines(keepends=True), filename)
    _index_file(path)
    return f"Created {filename}"


def tool_read_file(filename: str) -> str:
    path = Path(filename)
    if not path.exists():
        return f"Error: {filename} does not exist"
    content = path.read_text(errors="replace")
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + f"\n[truncated — file has {len(content)} chars total]"
    return content


def tool_delete_file(filename: str) -> str:
    path = Path(filename)
    if not path.exists():
        return f"Error: {filename} does not exist"
    old_lines = path.read_text(errors="replace").splitlines(keepends=True)
    path.unlink()
    _print_diff(old_lines, [], filename)
    _remove_from_index(filename)
    return f"Deleted {filename}"


def tool_list_files() -> str:
    entries = sorted(Path(".").iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return "Directory is empty"
    lines = [f"{'dir ' if p.is_dir() else 'file'} {p.name}" for p in entries]
    return "\n".join(lines)


def tool_edit_file(filename: str, find: str, replace: str) -> str:
    path = Path(filename)
    if not path.exists():
        return f"Error: {filename} does not exist"
    content = path.read_text(errors="replace")
    if find not in content:
        return f"Error: text not found in {filename}"
    old_lines = content.splitlines(keepends=True)
    new_content = content.replace(find, replace, 1)
    path.write_text(new_content)
    _print_diff(old_lines, new_content.splitlines(keepends=True), filename)
    _index_file(path)
    return f"Edited {filename}"


def tool_create_folder(folder_path: str) -> str:
    Path(folder_path).mkdir(parents=True, exist_ok=True)
    return f"Created folder {folder_path}"


def tool_move_file(src: str, dst: str) -> str:
    src_path = Path(src)
    if not src_path.exists():
        return f"Error: {src} does not exist"
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_path), str(dst_path))
    _remove_from_index(src)
    _index_file(dst_path)
    return f"Moved {src} → {dst}"


def tool_run_command(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        if len(output) > 3000:
            output = output[:3000] + "\n[output truncated]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s limit)"
    except Exception as e:
        return f"Error: {e}"


# ── Tool dispatch ─────────────────────────────────────────────────────────────

# Human-readable label + the key whose value names the target (or None)
ACTION_LABELS: dict[str, tuple[str, Optional[str]]] = {
    "create_file":   ("Creating",       "filename"),
    "read_file":     ("Reading",        "filename"),
    "delete_file":   ("Deleting",       "filename"),
    "list_files":    ("Listing files",  None),
    "edit_file":     ("Editing",        "filename"),
    "create_folder": ("Creating folder","path"),
    "move_file":     ("Moving",         "src"),
    "run_command":   ("Running",        "command"),
}

TOOLS = {
    "create_file":   lambda d: tool_create_file(d["filename"], d["content"]),
    "read_file":     lambda d: tool_read_file(d["filename"]),
    "delete_file":   lambda d: tool_delete_file(d["filename"]),
    "list_files":    lambda d: tool_list_files(),
    "edit_file":     lambda d: tool_edit_file(d["filename"], d["find"], d["replace"]),
    "create_folder": lambda d: tool_create_folder(d["path"]),
    "move_file":     lambda d: tool_move_file(d["src"], d["dst"]),
    "run_command":   lambda d: tool_run_command(d["command"]),
}


def extract_json(text: str) -> Optional[dict]:
    """
    Find and parse the first valid JSON object in a model reply.
    Handles markdown code fences and nested braces correctly.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _is_malformed_tool_reply(reply: str) -> bool:
    """
    Return True when the model's 'final' reply is actually a garbled tool call
    (e.g. pure JSON, or action-name + JSON without the action key inside).
    """
    stripped = reply.strip()
    if stripped.startswith("{"):
        return True
    for action in TOOLS:
        if action in stripped and "{" in stripped:
            return True
    return False


def try_dispatch(reply: str) -> Optional[str]:
    """
    If the reply contains a tool JSON, execute it and return the result.
    Returns None if the reply is plain text (no tool call).
    """
    data = extract_json(reply)
    if data is None:
        return None

    action = data.get("action")
    if action not in TOOLS:
        return None

    label, target_key = ACTION_LABELS.get(action, (action, None))
    target = data.get(target_key, "") if target_key else ""
    if target:
        console.print(f"[dim]  ↳ {label} [bold]{target}[/bold][/dim]")
    else:
        console.print(f"[dim]  ↳ {label}[/dim]")

    try:
        return TOOLS[action](data)
    except KeyError as e:
        return f"Error: missing argument {e}"


# ── Model call ────────────────────────────────────────────────────────────────

def call_model(messages: list[dict]) -> str:
    """Send a message list to Ollama and return the reply text."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "messages": messages, "stream": False},
            timeout=MODEL_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        console.print(
            "\n[red]Cannot connect to Ollama. Is it running?[/red]\n"
            "[dim]Start it with: ollama serve[/dim]"
        )
        sys.exit(1)
    except requests.exceptions.Timeout:
        return f"Error: model timed out ({MODEL_TIMEOUT}s)"
    except Exception as e:
        return f"Error calling model: {e}"


# ── Agent loop ────────────────────────────────────────────────────────────────

def run_turn(user_input: str) -> None:
    """
    Process one user message. Handles multi-step tool chaining:
    the model can call tools repeatedly until it produces a plain-text response.
    """
    # Query RAG memory for relevant context (empty string if RAG is off)
    context = memory_query(user_input)

    # Store the plain user message in history
    history.append({"role": "user", "content": user_input})

    for iteration in range(MAX_TOOL_ITERATIONS):
        # Build the message list for this model call.
        # On the first call, optionally prepend RAG context to the user's message.
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if iteration == 0 and context:
            # Inject context only for the first call of this turn.
            # We splice it into the last user message without polluting history.
            *prior_msgs, last_msg = history
            messages.extend(prior_msgs)
            messages.append({
                "role": "user",
                "content": f"[Context from local files:]\n{context}\n\n[User:] {last_msg['content']}",
            })
        else:
            messages.extend(history)

        reply = call_model(messages)
        history.append({"role": "assistant", "content": reply})

        tool_result = try_dispatch(reply)

        if tool_result is None:
            # If the model output raw JSON instead of a plain-text explanation,
            # ask it to explain in plain text instead of showing the JSON.
            if _is_malformed_tool_reply(reply):
                history.append({
                    "role": "user",
                    "content": "Summarize in plain text what you just did.",
                })
                messages2 = [{"role": "system", "content": SYSTEM_PROMPT}] + history
                follow_up = call_model(messages2)
                history.append({"role": "assistant", "content": follow_up})
                console.print(Panel(
                    follow_up.strip(),
                    border_style="bright_black",
                    padding=(0, 1),
                ))
            else:
                console.print(Panel(
                    reply.strip(),
                    border_style="bright_black",
                    padding=(0, 1),
                ))
            break
        else:
            # Feed the tool result back so the model can continue.
            preview = tool_result[:120] + ("..." if len(tool_result) > 120 else "")
            console.print(f"[dim]     → {preview}[/dim]")
            history.append({"role": "user", "content": f"[Tool result]: {tool_result}"})
    else:
        console.print("[yellow]maw > reached max tool steps, stopping.[/yellow]\n")

    save_history()


# ── /model command ────────────────────────────────────────────────────────────

KNOWN_MODELS: list[tuple[str, str, str]] = [
    ("tinyllama:1.1b",    "638MB",  "ultra-fast, minimal RAM"),
    ("gemma2:2b",         "1.6GB",  "Google's fast small model"),
    ("llama3.2:3b",       "2.0GB",  "Meta's capable 3B"),
    ("phi3:mini",         "2.2GB",  "strong at coding & reasoning"),
    ("qwen2.5:3b",        "2.0GB",  "great multilingual support"),
    ("mistral:7b",        "4.1GB",  "fast, great all-rounder"),
    ("qwen2.5:7b",        "4.4GB",  "excellent multilingual"),
    ("llama3.1:8b",       "4.7GB",  "Meta's best 8B"),
    ("deepseek-r1:8b",    "4.9GB",  "strong reasoning model"),
    ("gemma2:9b",         "5.5GB",  "Google's solid mid-size"),
    ("mistral-nemo:12b",  "7.1GB",  "Mistral's efficient 12B"),
    ("codellama:13b",     "7.4GB",  "code-focused"),
    ("qwen2.5:14b",       "8.9GB",  "very capable, great reasoning"),
    ("phi4:14b",          "8.5GB",  "Microsoft's strong 14B"),
    ("deepseek-r1:14b",   "9.0GB",  "advanced reasoning"),
    ("gemma2:27b",        "16GB",   "Google's large capable model"),
    ("codestral:22b",     "12.9GB", "Mistral's code specialist"),
    ("qwen2.5:32b",       "19GB",   "Qwen's most powerful"),
    ("deepseek-r1:32b",   "19GB",   "top-tier reasoning"),
    ("mixtral:8x7b",      "26GB",   "mixture-of-experts powerhouse"),
    ("qwen2.5-coder:32b", "19GB",   "Qwen's dedicated code model"),
    ("qwen2.5-coder:14b", "9.0GB",  "coder 14B — fast on 16GB RAM"),
]


def _pick(items: list[tuple[str, str]], header: str = "") -> Optional[str]:
    """
    Inline arrow-key picker. Items are (value, meta_text) pairs.
    Returns the selected value, or None if cancelled.
    """
    idx = [0]
    chosen: list[Optional[str]] = [None]

    def render() -> FormattedText:
        out: list[tuple[str, str]] = []
        if header:
            out.append(("bold ansiwhite", f"\n  {header}\n\n"))
        for i, (name, meta) in enumerate(items):
            if i == idx[0]:
                out.append(("bold fg:ansibrightblue", f"  ▶  {name:<26}"))
                if meta:
                    out.append(("fg:ansiblue", f"  {meta}"))
                out.append(("", "\n"))
            else:
                out.append(("fg:ansiblue", f"     {name}\n"))
        out.append(("fg:#555555", "\n  ↑↓ navigate  ·  Enter select  ·  Esc cancel\n"))
        return FormattedText(out)

    kb = KeyBindings()

    @kb.add("up")
    def _up(e):
        idx[0] = (idx[0] - 1) % len(items)

    @kb.add("down")
    def _down(e):
        idx[0] = (idx[0] + 1) % len(items)

    @kb.add("enter")
    def _enter(e):
        chosen[0] = items[idx[0]][0]
        e.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(e):
        e.app.exit()

    Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    ).run()

    return chosen[0]


def _ollama_list() -> list[str]:
    """Return names of installed Ollama models."""
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().splitlines()[1:]  # skip header row
        return [l.split()[0] for l in lines if l.strip()]
    except Exception:
        return []


def cmd_model(arg: str) -> None:
    global MODEL

    # ── /model install ────────────────────────────────────────────────────────
    if arg == "install":
        installed = _ollama_list()
        items = [
            (name, f"{size:<7}  {desc}" + ("  ✓" if name in installed else ""))
            for name, size, desc in KNOWN_MODELS
        ]
        target = _pick(items, "Install model  (↑↓ · Enter)")
        if not target:
            return
        console.print(f"\n[dim]  Pulling {target}…[/dim]\n")
        try:
            subprocess.run(["ollama", "pull", target], check=True)
            console.print(f"\n[green]  ✓  {target} installed[/green]")
            MODEL = target
            console.print(f"[dim]  Switched to {MODEL}[/dim]\n")
        except subprocess.CalledProcessError:
            console.print(f"[red]  Failed to pull {target}[/red]\n")
        return

    # ── /model (no arg) — pick from installed ────────────────────────────────
    if not arg:
        installed = _ollama_list()
        if not installed:
            console.print("[yellow]  No models found — is Ollama running?[/yellow]\n")
            return
        items = [(m, "← active" if m == MODEL else "") for m in installed]
        # pre-select the active model
        chosen = _pick(items, "Switch model  (↑↓ · Enter)")
        if chosen:
            MODEL = chosen
            console.print(f"[green]  Switched to [bold]{MODEL}[/bold][/green]\n")
        return

    # ── /model <name> — switch directly ──────────────────────────────────────
    MODEL = arg
    console.print(f"[green]  Switched to [bold]{MODEL}[/bold] for this session.[/green]\n")


# ── CLI commands ──────────────────────────────────────────────────────────────

def cmd_reset() -> None:
    """Clear conversation history and RAG memory for the current folder."""
    if MAW_DIR.exists():
        shutil.rmtree(MAW_DIR)
        console.print("[green]Reset complete — cleared history and RAG memory for this folder.[/green]")
    else:
        console.print("Nothing to reset (no .maw/ directory here).")


def cmd_help() -> None:
    console.print("""
[bold]Maw[/bold] — local AI file agent

[bold]Usage:[/bold]
  maw              Start an interactive session in the current folder
  maw reset        Clear conversation history and RAG memory for this folder
  maw --help       Show this help

[bold]Tools available to the model:[/bold]
  create_file, read_file, delete_file, list_files
  edit_file (find & replace), create_folder, move_file
  run_command (runs shell commands — use with care)

[bold]Memory:[/bold]
  Maw keeps a .maw/ folder in each directory it runs in.
  It stores your conversation history and a local vector index of text files.
  Use [bold]maw reset[/bold] to wipe it.

[bold]Requires:[/bold]
  Ollama running locally (ollama serve)
  Model: """ + MODEL + """

[bold]Tips:[/bold]
  Press Ctrl+C to exit at any time.
""")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default=None)
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args()

    if args.help:
        cmd_help()
        return

    if args.command == "reset":
        cmd_reset()
        return

    if args.command is not None:
        console.print(f"[red]Unknown command: {args.command}[/red]")
        cmd_help()
        return

    # ── Interactive mode ──────────────────────────────────────────────────────
    MAW_DIR.mkdir(exist_ok=True)
    load_history()
    init_rag()

    console.clear()
    console.print("[bold blue]  ███╗   ███╗ █████╗ ██╗    ██╗[/bold blue]")
    console.print("[bold blue]  ████╗ ████║██╔══██╗██║    ██║[/bold blue]")
    console.print("[bold blue]  ██╔████╔██║███████║██║ █╗ ██║[/bold blue]")
    console.print("[bold blue]  ██║╚██╔╝██║██╔══██║██║███╗██║[/bold blue]")
    console.print("[bold blue]  ██║ ╚═╝ ██║██║  ██║╚███╔███╔╝[/bold blue]")
    console.print("[bold blue]  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝ [/bold blue]")
    folder = Path('.').resolve().name
    console.print(
        f"\n  [bold white]local AI file agent[/bold white]  ·  [dim]{MODEL} · {folder}[/dim]\n"
        f"  [dim]Ctrl+C to exit · maw reset to clear memory[/dim]\n"
    )

    # ── Slash-command completer ───────────────────────────────────────────────
    class SlashCompleter(Completer):
        _commands = [
            ("/model",         "list installed models / switch active model"),
            ("/model install", "browse and install a new model"),
        ]

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            for cmd, meta in self._commands:
                if cmd.startswith(text):
                    yield Completion(
                        cmd[len(text):],
                        display=cmd,
                        display_meta=meta,
                    )

    # ── prompt_toolkit setup ──────────────────────────────────────────────────
    kb = KeyBindings()

    @kb.add("c-delete")
    def delete_word_forward(event):
        buf = event.app.current_buffer
        pos = buf.document.find_next_word_ending(count=1)
        if pos:
            buf.delete(count=pos)


    pt_style = PtStyle.from_dict({
        "prompt": "bold ansicyan",
        # Completion dropdown — transparent background, Maw blue text
        "completion-menu":                        "bg:default",
        "completion-menu.completion":             "fg:ansiblue bg:default",
        "completion-menu.completion.current":     "fg:ansibrightblue bg:default bold",
        "completion-menu.meta.completion":        "fg:ansiblue bg:default",
        "completion-menu.meta.completion.current":"fg:ansibrightblue bg:default",
        "scrollbar.background":                   "bg:default",
        "scrollbar.button":                       "bg:ansiblue",
    })
    _history = InMemoryHistory()

    while True:
        try:
            user_input = pt_prompt(
                [("class:prompt", " > ")],
                history=_history,
                key_bindings=kb,
                style=pt_style,
                completer=SlashCompleter(),
                complete_while_typing=True,
            ).strip()
            if not user_input:
                continue
            if user_input.startswith("/model"):
                cmd_model(user_input[len("/model"):].strip())
                continue
            run_turn(user_input)
        except KeyboardInterrupt:
            console.print("\n[dim]Shutting down.[/dim]")
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()
