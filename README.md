# Maw

```
  ███╗   ███╗ █████╗ ██╗    ██╗
  ████╗ ████║██╔══██╗██║    ██║
  ██╔████╔██║███████║██║ █╗ ██║
  ██║╚██╔╝██║██╔══██║██║███╗██║
  ██║ ╚═╝ ██║██║  ██║╚███╔███╔╝
  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝
```

A local AI agent that lives in your terminal. It reads and writes files, runs commands, remembers context across sessions — all powered by [Ollama](https://ollama.com). No cloud. No API keys. No subscription.

> Runs on Linux and macOS. Windows users need [WSL](https://learn.microsoft.com/en-us/windows/wsl/install).

---

## Install

```bash
git clone https://github.com/tadeycek/maw
cd maw
bash install.sh
```

The installer handles everything:

- Checks for Python 3.10+ and Ollama (installs if missing)
- Creates a virtual environment and installs dependencies
- Optionally installs RAG memory support (chromadb + sentence-transformers, ~500MB)
- Lets you pick from 21 models based on your available RAM
- Adds a `maw` command to `~/.local/bin`

No need to activate a virtual environment — the `maw` command handles it automatically.

---

## Usage

```bash
maw              # start a session in the current folder
maw reset        # wipe memory and history for the current folder
maw --help       # show help
```

Run `maw` from any folder. It scopes everything — memory, history, file context — to that directory.

---

## What Maw can do

Maw understands natural language and automatically uses the right tools:

| Tool | Description |
|---|---|
| `read_file` | Read any file in the current directory tree |
| `create_file` | Create a new file with content |
| `edit_file` | Find and replace text in a file |
| `delete_file` | Delete a file |
| `list_files` | List files in a directory |
| `create_folder` | Create a new directory |
| `move_file` | Move or rename a file |
| `run_command` | Run a shell command and return output |

Examples of things you can ask:

```
> summarize all the Python files in this folder
> create a README for this project
> find all TODO comments and list them
> rename every file that starts with "draft_" to remove the prefix
> run the tests and tell me what failed
```

---

## Memory

Maw stores a `.maw/` folder inside each directory it runs in:

```
.maw/
├── history.json     # full conversation history (persists across restarts)
└── chroma/          # local vector index of your files (if RAG is enabled)
```

When RAG is enabled, Maw indexes the text files in your folder on startup and retrieves relevant context automatically. This lets it answer questions about large codebases without hitting token limits.

Run `maw reset` to wipe memory for the current folder and start fresh.

---

## Models

21 models are available to choose from during install. The installer detects your RAM and recommends the best fit.

**Recommended defaults:**

| RAM | Model | Size |
|---|---|---|
| < 8GB | llama3.2:3b | 2.0GB |
| 8–20GB | llama3.1:8b | 4.7GB |
| 20–40GB | qwen2.5:14b | 8.9GB |
| 40GB+ | qwen2.5:32b | 19GB |

**Full model list:**

| # | Model | Size | Notes |
|---|---|---|---|
| 1 | tinyllama:1.1b | 638MB | Ultra-fast, minimal RAM |
| 2 | gemma2:2b | 1.6GB | Google's fast small model |
| 3 | llama3.2:3b | 2.0GB | Meta's capable 3B |
| 4 | phi3:mini | 2.2GB | Strong at coding & reasoning |
| 5 | qwen2.5:3b | 2.0GB | Great multilingual support |
| 6 | mistral:7b | 4.1GB | Fast, great all-rounder |
| 7 | qwen2.5:7b | 4.4GB | Excellent multilingual |
| 8 | llama3.1:8b | 4.7GB | Meta's best 8B |
| 9 | deepseek-r1:8b | 4.9GB | Strong reasoning model |
| 10 | gemma2:9b | 5.5GB | Google's solid mid-size |
| 11 | mistral-nemo:12b | 7.1GB | Mistral's efficient 12B |
| 12 | codellama:13b | 7.4GB | Code-focused |
| 13 | qwen2.5:14b | 8.9GB | Very capable, great reasoning |
| 14 | phi4:14b | 8.5GB | Microsoft's strong 14B |
| 15 | deepseek-r1:14b | 9.0GB | Advanced reasoning |
| 16 | gemma2:27b | 16GB | Google's large capable model |
| 17 | codestral:22b | 12.9GB | Mistral's code specialist |
| 18 | qwen2.5:32b | 19GB | Qwen's most powerful |
| 19 | deepseek-r1:32b | 19GB | Top-tier reasoning |
| 20 | mixtral:8x7b | 26GB | Mixture-of-experts powerhouse |
| 21 | qwen2.5-coder:32b | 19GB | Qwen's dedicated code model |

To switch models after install, run `install.sh` again or edit the `MODEL` line in `agent.py`.

---

## How it works

Maw is a single Python file (`agent.py`, ~350 lines) with no frameworks. The core loop:

1. You send a message
2. The model decides whether to call a tool
3. Tool output is fed back as context
4. The loop repeats until the model responds with plain text (max 10 iterations)

No LangChain. No agents framework. Just a straightforward tool-use loop built on the Ollama API.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) (installed automatically if missing)
- 2GB+ disk space for the smallest model, 20GB+ for larger ones
