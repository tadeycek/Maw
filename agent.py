#!/usr/bin/env python3
"""
Maw — a local AI file agent powered by Ollama.
Runs in any folder, manages files, runs commands, and remembers context.
"""

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
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PtStyle

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
MAW_DIR = Path(".maw")
HISTORY_FILE = MAW_DIR / "history.json"
MAX_TOOL_ITERATIONS = 10          # guard against infinite tool loops
MAX_FILE_CHARS = 4000             # truncate large files before sending to model
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
- After a [Tool result] is given to you, use it to continue working or give a final plain-text answer.
- Never narrate what you are about to do. Just do it.
- Only use plain text when no tool is needed (e.g. answering questions).

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
→ Done. Rewrote hello.txt in uppercase."""

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


def memory_query(query: str, n: int = 3) -> str:
    """Return the most relevant file snippets for a query, or '' if RAG is off."""
    if not _rag_enabled or _collection.count() == 0:
        return ""
    try:
        results = _collection.query(
            query_embeddings=[_embed(query)],
            n_results=min(n, _collection.count()),
        )
        parts = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
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


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_create_file(filename: str, content: str) -> str:
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
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
    path.unlink()
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
    path.write_text(content.replace(find, replace, 1))
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

    args_str = ", ".join(
        f"{k}={repr(v)[:40]}" for k, v in data.items() if k != "action"
    )
    console.print(f"[dim]  ⚙ {action}({args_str})[/dim]")

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
            timeout=120,
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
        return "Error: model timed out (120s)"
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
            # Model gave a plain-text response — we're done with this turn.
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

    folder = Path('.').resolve().name
    console.print(
        f"\n[bold white]Maw[/bold white] [dim]({MODEL} · {folder})[/dim]\n"
        f"[dim]Ctrl+C to exit · maw reset to clear memory[/dim]\n"
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
    })
    _history = InMemoryHistory()

    while True:
        try:
            user_input = pt_prompt(
                [("class:prompt", " > ")],
                history=_history,
                key_bindings=kb,
                style=pt_style,
            ).strip()
            if not user_input:
                continue
            run_turn(user_input)
        except KeyboardInterrupt:
            console.print("\n[dim]Shutting down.[/dim]")
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()
