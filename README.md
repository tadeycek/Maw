# Maw

A local AI file agent that runs in your terminal. Manages files, runs commands, and remembers context — powered entirely by Ollama (no cloud, no API keys).

```
  ███╗   ███╗ █████╗ ██╗    ██╗
  ████╗ ████║██╔══██╗██║    ██║
  ██╔████╔██║███████║██║ █╗ ██║
  ██║╚██╔╝██║██╔══██║██║███╗██║
  ██║ ╚═╝ ██║██║  ██║╚███╔███╔╝
  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

## Install

```bash
git clone https://github.com/tadeycek/maw
cd maw
bash install.sh
```

The installer will:
- Check Python and Ollama
- Create a virtual environment
- Install dependencies
- Optionally install RAG memory support (chromadb + sentence-transformers, ~500MB)
- Let you pick a model based on your available RAM
- Install a `maw` command to `~/.local/bin`

You don't need to activate any virtual environment — the `maw` command handles everything.

## Usage

```bash
maw              # start a session in the current folder
maw reset        # clear memory and history for the current folder
maw --help       # show help
```

Run `maw` from any folder. It scopes its memory to that directory.

## What it can do

Maw has access to these tools and will use them automatically:

| Tool | What it does |
|---|---|
| `create_file` | Create a new file with content |
| `read_file` | Read a file |
| `edit_file` | Find and replace text in a file |
| `delete_file` | Delete a file |
| `list_files` | List files in the current directory |
| `create_folder` | Create a directory |
| `move_file` | Move or rename a file |
| `run_command` | Run a shell command |

## Memory

Maw stores a `.maw/` folder in each directory it runs in, containing:
- `history.json` — conversation history (persists across restarts)
- `chroma/` — local vector index of text files (if RAG is enabled)

Run `maw reset` to wipe it for the current folder.

## Models

Recommended models (picked during install):

| RAM | Model | Size |
|---|---|---|
| < 8GB | llama3.2:3b | 2.0GB |
| 8–20GB | llama3.1:8b | 4.7GB |
| 20GB+ | qwen2.5:14b | 8.9GB |

To switch models after install, run `install.sh` again or edit the `MODEL` line in `agent.py`.
